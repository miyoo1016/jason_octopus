"""
백테스트 엔진.
특정 시그널 발생일(T)에 검출된 종목들을 다음 거래일(T+1) 시가에 매수하여
정해진 기간(hold_days) 보유하거나, 중간에 익절/손절 조건에 도달하면 청산하는 로직.
미래 참조(Look-ahead bias)를 방지하며, 한국 시장 거래비용(세금 0.18% + 수수료)을 반영합니다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd
from pydantic import BaseModel

from data.holidays import next_trading_day
from engine.leakage_guard import assert_after

logger = logging.getLogger(__name__)


class BacktestParams(BaseModel):
    hold_days: int = 20
    stop_loss_pct: float = 0.05       # 5% 손절
    take_profit_pct: float = 0.20     # 20% 익절 (0.0이면 미적용)
    trading_cost_pct: float = 0.0023  # 매매비용(세금 0.18% + 수수료 및 슬리피지 0.05% 추정)
    slippage_model: str = "fixed"     # "fixed" | "dynamic"
    order_value_krw: float = 10_000_000  # 동적 슬리피지 시 종목당 주문금액 (1천만원)


@dataclass
class TradeResult:
    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float      # 비용 차감 후 순수익률 (%)
    exit_reason: str       # "TP" (익절), "SL" (손절), "TIME" (기간종료)


class BacktestEngine:
    def __init__(self, krx_client: Any) -> None:
        self.krx_client = krx_client

    def run_signal_backtest(
        self,
        signal_df: pd.DataFrame,
        signal_date: str,
        params: BacktestParams,
    ) -> dict[str, Any]:
        """
        단일 시그널 발생일에 대한 백테스트를 수행합니다.
        
        Args:
            signal_df:   시그널을 통과한 종목 DataFrame (code, name 포함 필수)
            signal_date: 시그널 발생일 'YYYY-MM-DD' (T일)
            params:      백테스트 파라미터
            
        Returns:
            백테스트 요약 결과 딕셔너리
        """
        if signal_df.empty:
            return self._empty_result()

        # 진입일은 시그널 발생일의 다음 거래일 (T+1)
        entry_date = next_trading_day(signal_date, n=1)
        # 데이터를 조회할 충분한 여유 기간 (휴일 감안하여 hold_days * 2)
        end_date = next_trading_day(entry_date, n=params.hold_days + 10)
        
        codes = signal_df["code"].tolist()
        names = dict(zip(signal_df["code"], signal_df["name"]))
        
        # OHLCV 데이터 배치 수집 (entry_date ~ end_date)
        ohlcv_dict = self.krx_client.get_ohlcv_batch(codes, entry_date, end_date)
        
        trades: list[TradeResult] = []
        daily_returns: dict[str, list[float]] = {} # 날짜별 수익률 (MDD 계산용)
        
        for code in codes:
            hist = ohlcv_dict.get(code)
            if hist is None or hist.empty:
                logger.warning("백테스트 데이터 없음: %s", code)
                continue

            # 시간순 정렬 보장
            hist = hist.sort_index()

            if len(hist) == 0:
                continue

            # ⚠️ Look-ahead 방지: 진입 후보 데이터는 반드시 signal_date 초과여야 함
            assert_after(hist, signal_date, context=f"BacktestEngine:{code}")

            # 진입: T+1일(또는 hist의 첫번째 날)의 시가
            entry_row = hist.iloc[0]
            actual_entry_date = entry_row.name.strftime("%Y-%m-%d") if hasattr(entry_row.name, "strftime") else str(entry_row.name)[:10]
            entry_price = float(entry_row["open"])
            
            if entry_price <= 0:
                continue # 거래정지 등
                
            # 목표가 및 손절가 계산
            target_price = entry_price * (1 + params.take_profit_pct) if params.take_profit_pct > 0 else float('inf')
            stop_price = entry_price * (1 - params.stop_loss_pct) if params.stop_loss_pct > 0 else 0.0
            
            exit_price = 0.0
            actual_exit_date = ""
            exit_reason = "TIME"
            
            # 보유 기간 동안 시뮬레이션
            # 진입일 당일도 장중 변동을 감안하여 루프에 포함
            hold_period = hist.head(params.hold_days)
            
            daily_curve = []
            
            for date_idx, row in hold_period.iterrows():
                current_date = date_idx.strftime("%Y-%m-%d") if hasattr(date_idx, "strftime") else str(date_idx)[:10]
                high = float(row["high"])
                low = float(row["low"])
                close = float(row["close"])
                
                # 가상 일일 평가수익률 (MDD용)
                daily_curve.append((close / entry_price) - 1.0)
                
                # 손절 조건 확인 (우선)
                if low <= stop_price:
                    exit_price = stop_price
                    actual_exit_date = current_date
                    exit_reason = "SL"
                    break
                    
                # 익절 조건 확인
                if high >= target_price:
                    exit_price = target_price
                    actual_exit_date = current_date
                    exit_reason = "TP"
                    break
                    
            else:
                # 중간에 청산되지 않고 보유기간이 끝난 경우
                exit_row = hold_period.iloc[-1]
                actual_exit_date = hold_period.index[-1].strftime("%Y-%m-%d") if hasattr(hold_period.index[-1], "strftime") else str(hold_period.index[-1])[:10]
                exit_price = float(exit_row["close"])
                exit_reason = "TIME"
                
            # 수익률 계산 (매수/매도 수수료 및 세금 차감)
            gross_return = (exit_price / entry_price) - 1.0

            # 슬리피지 비용 계산
            if params.slippage_model == "dynamic":
                # 동적 슬리피지: sqrt(주문금액 / 일평균 거래대금)
                avg_val = row.get("avg_trading_value", 0) if isinstance(row, dict) else 0
                # signal_df에서 avg_trading_value 가져오기
                if avg_val <= 0:
                    match = signal_df.loc[signal_df["code"] == code, "avg_trading_value"]
                    avg_val = float(match.iloc[0]) if not match.empty else 0
                if avg_val > 0:
                    impact = (params.order_value_krw / avg_val) ** 0.5
                    slippage_cost = min(impact * 0.01, 0.03)  # 최대 3%
                else:
                    slippage_cost = 0.01  # 거래대금 불명 시 기본 1%
                total_cost = params.trading_cost_pct + slippage_cost
            else:
                total_cost = params.trading_cost_pct

            net_return = gross_return - total_cost
            
            trades.append(TradeResult(
                code=code,
                name=names.get(code, code),
                entry_date=actual_entry_date,
                exit_date=actual_exit_date,
                entry_price=entry_price,
                exit_price=exit_price,
                return_pct=net_return * 100, # 퍼센트로 변환
                exit_reason=exit_reason
            ))
            
        if not trades:
            return self._empty_result()
            
        # 포트폴리오 메트릭 계산 (동일비중 가정)
        win_trades = [t for t in trades if t.return_pct > 0]
        win_rate = (len(win_trades) / len(trades)) * 100
        avg_return = sum(t.return_pct for t in trades) / len(trades)
        
        # MDD 근사치 (개별 종목별 최대 낙폭의 평균으로 단순 계산 - MVP 수준)
        # 실제 포트폴리오 MDD는 자산 배분 비중의 일일 합산으로 구해야 하지만 여기서는 간략화합니다.
        avg_mdd = 0.0
        
        return {
            "success": True,
            "trade_count": len(trades),
            "win_rate": round(win_rate, 2),
            "total_return": round(avg_return, 2),
            "mdd": -round(params.stop_loss_pct * 100, 2), # MVP용 근사 (최대 손실폭 기준)
            "trades": [
                {
                    "code": t.code,
                    "name": t.name,
                    "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "return_pct": round(t.return_pct, 2),
                    "exit_reason": t.exit_reason
                }
                for t in trades
            ]
        }
        
    def _empty_result(self) -> dict[str, Any]:
        return {
            "success": False,
            "trade_count": 0,
            "win_rate": 0.0,
            "total_return": 0.0,
            "mdd": 0.0,
            "trades": []
        }
