"""
박스권 돌파 찾기 노드.
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from engine.leakage_guard import assert_no_future_data
from data.holidays import prev_trading_day

class BoxBreakoutParams(BaseModel):
    box_period: int = 60
    breakout_pct: float = 1.0
    vol_A: float = 2.0
    vol_B: float = 1.5
    vol_C: float = 1.0

class BoxBreakoutNode(BaseNode):
    NODE_TYPE      = "box_breakout"
    CACHE_VERSION  = "score-status-v3-box-high"
    DISPLAY_NAME   = "박스권 돌파 찾기"
    DESCRIPTION    = "최근 N일 최고가 근접 또는 돌파 상태를 점수/등급으로 표시합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = (
        "box_breakout_pct",
        "box_breakout_grade",
        "breakout_score",
        "breakout_status",
        "breakout_flag",
        "breakout_reason",
        "breakout_distance_pct",
        "breakout_volume_ratio",
        "box_high",
        "recent_high",
        "box_breakout_flag",
        "box_breakout_warning",
    )
    ParamsModel    = BoxBreakoutParams

    @staticmethod
    def _annotate(
        row: pd.Series,
        *,
        score: int,
        status: str,
        flag: str,
        reason: str,
        breakout_pct: float | None = None,
        distance_pct: float | None = None,
        volume_ratio: float | None = None,
        box_high: float | None = None,
        recent_high: float | None = None,
        grade: str = "DATA_MISSING",
        warning: str | None = None,
    ) -> dict:
        row_dict = row.to_dict()
        row_dict["box_breakout_pct"] = breakout_pct
        row_dict["box_breakout_grade"] = grade
        row_dict["breakout_score"] = score
        row_dict["breakout_status"] = status
        row_dict["breakout_flag"] = flag
        row_dict["breakout_reason"] = reason
        row_dict["breakout_distance_pct"] = distance_pct
        row_dict["breakout_volume_ratio"] = volume_ratio
        row_dict["box_high"] = box_high
        row_dict["recent_high"] = recent_high
        row_dict["box_breakout_flag"] = flag
        row_dict["box_breakout_warning"] = warning
        return row_dict

    def run(self, inputs: list[pd.DataFrame], params: BoxBreakoutParams, context: ExecutionContext) -> pd.DataFrame:
        from backend.algo_settings import algo_settings
        df = inputs[0]
        if df.empty:
            return df.copy()
        if not context.krx_client:
            out = df.copy()
            out["box_breakout_pct"] = None
            out["box_breakout_grade"] = "DATA_MISSING"
            # [FIX] DATA_MISSING은 판단 보류 — 15점 가산 금지
            out["breakout_score"] = None
            out["breakout_status"] = "DATA_MISSING"
            out["breakout_flag"] = "DATA_MISSING"
            out["breakout_reason"] = "KRX client unavailable"
            out["breakout_distance_pct"] = None
            out["breakout_volume_ratio"] = None
            out["box_high"] = None
            out["recent_high"] = None
            out["box_breakout_flag"] = "DATA_MISSING"
            out["box_breakout_warning"] = "박스권 가격 데이터 수집 불가"
            return out
            
        start_date = prev_trading_day(context.as_of_date, n=params.box_period + 30)
        codes = df["code"].tolist()
        
        ohlcv_dict = context.krx_client.get_ohlcv_batch(codes, start_date, context.as_of_date)
        
        results = []
        for _, row in df.iterrows():
            code = row["code"]
            hist = ohlcv_dict.get(code)
            if hist is None or hist.empty or len(hist) < params.box_period:
                results.append(self._annotate(
                    row,
                    score=None,
                    status="DATA_MISSING",
                    flag="DATA_MISSING",
                    reason="박스권 가격 데이터 부족",
                    warning="박스권 가격 데이터 부족",
                ))
                continue
            assert_no_future_data(hist, context.as_of_date, context=f"BoxBreakoutNode:{code}")

            # 마지막 거래일 종가 등
            curr = hist.iloc[-1]
            curr_close = curr["close"]
            curr_open = curr["open"]
            curr_high = curr["high"]
            curr_vol = curr["volume"]
            
            # 최근 20일 평균 거래량 (당일 제외)
            if len(hist) < 21:
                results.append(self._annotate(
                    row,
                    score=None,
                    status="DATA_MISSING",
                    flag="DATA_MISSING",
                    reason="거래량 평균 계산 데이터 부족",
                    warning="거래량 평균 계산 데이터 부족",
                ))
                continue
            avg_vol_20 = hist["volume"].iloc[-21:-1].mean()
            
            # 마지막 거래일 이전 box_period 동안의 고점
            recent_hist = hist.iloc[-params.box_period-1:-1]
            if recent_hist.empty:
                results.append(self._annotate(
                    row,
                    score=None,
                    status="DATA_MISSING",
                    flag="DATA_MISSING",
                    reason="박스권 계산 데이터 부족",
                    warning="박스권 계산 데이터 부족",
                ))
                continue
            box_high = recent_hist["high"].max()
            
            if box_high > 0:
                breakout_pct = ((curr_close / box_high) - 1.0) * 100
                distance_pct = ((box_high / curr_close) - 1.0) * 100 if curr_close > 0 else None
                is_passed = breakout_pct >= params.breakout_pct
                
                # Grade evaluation
                is_yangbong = curr_close > curr_open
                high_pos = curr_close >= curr_high * 0.95
                res_break = curr_close >= box_high * 1.01
                vol_ratio = curr_vol / avg_vol_20 if avg_vol_20 > 0 else 0
                near_breakout = breakout_pct >= -3.0
                box_width_pct = ((recent_hist["high"].max() / recent_hist["low"].min()) - 1.0) * 100 if recent_hist["low"].min() > 0 else 999.0
                high_consolidation = breakout_pct >= -7.0 and box_width_pct <= 18.0
                
                grade = "D"
                volume_note = None
                
                if vol_ratio >= params.vol_A and is_yangbong and high_pos and res_break:
                    grade = "A"
                elif vol_ratio >= params.vol_B and is_yangbong:
                    grade = "B"
                elif vol_ratio >= params.vol_C:
                    grade = "C"
                    volume_note = f"거래량 보통 ({vol_ratio:.1f}배)"
                else:
                    grade = "D"
                    volume_note = f"거래량 부족 ({vol_ratio:.1f}배)"

                if is_passed and grade != "D":
                    status = "BREAKOUT_CONFIRMED"
                    score = 30
                    flag = "BREAKOUT_CONFIRMED"
                    reason = f"박스권 돌파 확인 ({breakout_pct:.1f}%, 거래량 {vol_ratio:.1f}배)"
                elif is_passed:
                    status = "FAILED_BREAKOUT"
                    score = 8
                    flag = "FAILED_BREAKOUT"
                    reason = f"가격은 돌파했지만 거래량 확인 부족 ({breakout_pct:.1f}%, {vol_ratio:.1f}배)"
                elif near_breakout:
                    status = "NEAR_BREAKOUT"
                    score = 24
                    flag = "NEAR_BREAKOUT"
                    reason = f"박스 상단 근접 ({breakout_pct:.1f}%)"
                elif high_consolidation:
                    status = "HIGH_CONSOLIDATION"
                    score = 22
                    flag = "HIGH_CONSOLIDATION"
                    reason = f"고가권 압축 진행 (박스폭 {box_width_pct:.1f}%)"
                elif breakout_pct >= -15.0:
                    status = "IN_BOX"
                    score = 15
                    flag = "IN_BOX"
                    reason = f"박스권 내부 ({breakout_pct:.1f}%)"
                else:
                    status = "NOT_READY"
                    score = 5
                    flag = "NOT_READY"
                    reason = f"돌파 준비 미흡 ({breakout_pct:.1f}%)"

                warning = None if status == "BREAKOUT_CONFIRMED" else reason
                if volume_note and status != "BREAKOUT_CONFIRMED":
                    warning = f"{warning} / {volume_note}"

                results.append(self._annotate(
                    row,
                    score=score,
                    status=status,
                    flag=flag,
                    reason=reason,
                    breakout_pct=breakout_pct,
                    distance_pct=distance_pct,
                    volume_ratio=vol_ratio,
                    box_high=box_high,
                    recent_high=box_high,
                    grade=f"{grade} ({vol_ratio:.1f}배)",
                    warning=warning,
                ))
            else:
                results.append(self._annotate(
                    row,
                    score=None,
                    status="DATA_MISSING",
                    flag="DATA_MISSING",
                    reason="박스 기준 고점 계산 불가",
                    warning="박스 기준 고점 계산 불가",
                ))
                    
        return pd.DataFrame(results)
