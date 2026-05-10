"""
거시 환경 필터 노드.
"""
import json
import logging
from pathlib import Path

import pandas as pd
import yfinance as yf
from pydantic import BaseModel

from backend.algo_settings import algo_settings
from backend.market_regime import calculate_market_regime
from engine.node_base import BaseNode, ExecutionContext

logger = logging.getLogger(__name__)

_MACRO_CACHE_PATH = Path(__file__).parent.parent / "data" / "cache" / "macro_last_known.json"

def _load_macro_cache() -> dict | None:
    """마지막으로 성공한 거시 데이터를 불러옵니다."""
    try:
        if _MACRO_CACHE_PATH.exists():
            return json.loads(_MACRO_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None

def _save_macro_cache(vix: float, sp500_up: bool, kospi_up: bool, as_of_date: str) -> None:
    """거시 데이터 수집 성공 시 캐시에 저장합니다."""
    try:
        _MACRO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MACRO_CACHE_PATH.write_text(
            json.dumps({
                "vix": vix, "sp500_up": sp500_up, "kospi_up": kospi_up,
                "cached_at": as_of_date,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("macro 캐시 저장 실패: %s", e)

class MacroFilterParams(BaseModel):
    pass

class MacroFilterNode(BaseNode):
    NODE_TYPE      = "macro_filter"
    DISPLAY_NAME   = "매크로 환경 분석"
    DESCRIPTION    = "VIX, S&P500, KOSPI 추세를 분석합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = (
        "macro_vix", "sp500_200ma_up", "kospi_200ma_up", "macro_score", "macro_status", "macro_flag", "macro_warning",
        "risk_on_prob", "neutral_prob", "risk_off_prob", "crisis_prob", "dominant_regime", "secondary_regime",
        "regime_as_of", "regime_data_sources", "regime_data_status", "regime_missing_inputs",
    )
    ParamsModel    = MacroFilterParams

    def run(self, inputs: list[pd.DataFrame], params: MacroFilterParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0].copy()
        if df.empty:
            df["macro_vix"] = pd.Series(dtype=float)
            df["sp500_200ma_up"] = pd.Series(dtype=bool)
            df["kospi_200ma_up"] = pd.Series(dtype=bool)
            df["macro_score"] = pd.Series(dtype=int)
            df["macro_status"] = pd.Series(dtype=object)
            df["macro_flag"] = pd.Series(dtype=object)
            df["macro_warning"] = pd.Series(dtype=object)
            df["dominant_regime"] = pd.Series(dtype=object)
            return df
            
        as_of_date = context.as_of_date
        
        # yfinance로 VIX와 S&P500(^GSPC) 데이터 가져오기
        # 200일 이평선을 구하기 위해 1년(약 252 거래일) 이상의 데이터를 가져옵니다.
        try:
            end_date = pd.to_datetime(as_of_date) + pd.Timedelta(days=1)
            start_date = end_date - pd.Timedelta(days=410)
            
            start_s = start_date.strftime("%Y-%m-%d")
            end_s = end_date.strftime("%Y-%m-%d")

            # yf.download 대신 Ticker().history() 사용 (단일 심볼 시 더 안정적일 때가 많음)
            def fetch_yf(symbol):
                try:
                    t = yf.Ticker(symbol)
                    h = t.history(start=start_s, end=end_s, interval="1d", timeout=10)
                    if h.empty:
                        # 재시도: period 사용
                        h = t.history(period="2y", interval="1d", timeout=10)
                        h = h[h.index <= end_date].tail(300)
                    return h["Close"] if not h.empty else pd.Series(dtype=float)
                except Exception as e:
                    logger.warning(f"yf {symbol} fetch error: {e}")
                    return pd.Series(dtype=float)

            vix_hist = fetch_yf("^VIX")
            current_vix = vix_hist.iloc[-1] if not vix_hist.empty else None
            
            sp500_hist = fetch_yf("^GSPC")
            sp500_up = None
            if len(sp500_hist) >= 200:
                sp500_close = sp500_hist.iloc[-1]
                sp500_200ma = sp500_hist.rolling(200).mean().iloc[-1]
                sp500_up = sp500_close > sp500_200ma
            elif not sp500_hist.empty:
                sp500_up = sp500_hist.iloc[-1] > sp500_hist.iloc[0]
                
            # KOSPI (context.krx_client 사용)
            kospi_up = None
            if context.krx_client:
                # 069500 (KODEX 200)로 추세 확인
                k_start = (pd.to_datetime(as_of_date) - pd.Timedelta(days=410)).strftime("%Y-%m-%d")
                k_hist_dict = context.krx_client.get_ohlcv_batch(["069500"], k_start, as_of_date)
                k_hist = k_hist_dict.get("069500")
                if k_hist is not None and not k_hist.empty:
                    if len(k_hist) >= 200:
                        kospi_close = k_hist['close'].iloc[-1]
                        kospi_200ma = k_hist['close'].rolling(200).mean().iloc[-1]
                        kospi_up = kospi_close > kospi_200ma
                    else:
                        kospi_up = k_hist['close'].iloc[-1] > k_hist['close'].iloc[0]

            # 하나라도 None이면(수집 실패) 캐시 폴백 시도
            if current_vix is None or sp500_up is None:
                # 캐시 폴백은 아래 except에서 처리되도록 유도
                raise ValueError(f"핵심 데이터 누락 (VIX:{current_vix}, SP500:{sp500_up})")

            macro_warning = None
            if current_vix > algo_settings.vix_hard_block:
                macro_warning = f"VIX({current_vix:.1f})가 경계치({algo_settings.vix_hard_block})를 초과했습니다."

            macro_score = 50
            if current_vix <= 20 and bool(sp500_up):
                macro_score = 65
            elif current_vix >= 30 or not bool(sp500_up):
                macro_score = 30

            df["macro_vix"]      = round(float(current_vix), 2)
            df["sp500_200ma_up"] = bool(sp500_up)
            df["kospi_200ma_up"] = bool(kospi_up) if kospi_up is not None else True
            df["macro_score"]    = macro_score
            df["macro_warning"]  = macro_warning
            df["macro_status"]   = "Active"
            df["macro_flag"]     = "RISK_ON" if macro_score >= 50 else "RISK_OFF"
            regime = calculate_market_regime(
                vix=float(current_vix),
                sp500_up=bool(sp500_up),
                kospi_up=bool(kospi_up) if kospi_up is not None else None,
                macro_status="Active",
                as_of_date=as_of_date,
            )
            for key, value in regime.items():
                df[key] = [value] * len(df) if isinstance(value, (list, dict)) else value
            
            # 성공 시 캐시 저장
            _save_macro_cache(current_vix, bool(sp500_up), bool(kospi_up), as_of_date)

        except Exception as e:
            logger.warning("MacroFilterNode 수집 실패: %s — 캐시 폴백 시도", e)
            cached = _load_macro_cache()
            if cached:
                logger.info("macro 캐시 폴백 사용: %s 기준", cached.get("cached_at", "?"))
                df["macro_vix"]      = cached.get("vix")
                df["sp500_200ma_up"] = cached.get("sp500_up")
                df["kospi_200ma_up"] = cached.get("kospi_up")
                df["macro_score"]    = 50
                df["macro_status"]   = "Cached"
                df["macro_flag"]     = "DATA_MISSING"
                df["macro_warning"]  = (
                    f"⚠️ 거시 데이터: {cached.get('cached_at', '?')} 기준 캐시 사용 중"
                )
                regime = calculate_market_regime(
                    vix=cached.get("vix"),
                    sp500_up=cached.get("sp500_up"),
                    kospi_up=cached.get("kospi_up"),
                    macro_status="Cached",
                    as_of_date=str(cached.get("cached_at", as_of_date)),
                )
                for key, value in regime.items():
                    df[key] = [value] * len(df) if isinstance(value, (list, dict)) else value
            else:
                # 최후의 수단: 기본값 (Risk-ON 가정하에 경고 표시)
                logger.error("macro 캐시 없음 — 기본값 강제 할당")
                df["macro_vix"]      = 15.0
                df["sp500_200ma_up"] = True
                df["kospi_200ma_up"] = True
                df["macro_score"]    = 50
                df["macro_status"]   = "Default"
                df["macro_flag"]     = "DATA_MISSING"
                df["macro_warning"]  = "🚨 거시 데이터 수집 불가 - UNKNOWN/중립값 적용"
                regime = calculate_market_regime(
                    vix=None,
                    sp500_up=None,
                    kospi_up=None,
                    macro_status="Default",
                    as_of_date=as_of_date,
                )
                for key, value in regime.items():
                    df[key] = [value] * len(df) if isinstance(value, (list, dict)) else value

        return df
