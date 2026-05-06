"""
거시 환경 필터 노드.
"""
import pandas as pd
import yfinance as yf
from pydantic import BaseModel
import logging
from engine.node_base import BaseNode, ExecutionContext
from backend.algo_settings import algo_settings

logger = logging.getLogger(__name__)

class MacroFilterParams(BaseModel):
    pass

class MacroFilterNode(BaseNode):
    NODE_TYPE      = "macro_filter"
    DISPLAY_NAME   = "매크로 환경 분석"
    DESCRIPTION    = "VIX, S&P500, KOSPI 추세를 분석합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("macro_vix", "sp500_200ma_up", "kospi_200ma_up", "macro_warning")
    ParamsModel    = MacroFilterParams

    def run(self, inputs: list[pd.DataFrame], params: MacroFilterParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0].copy()
        if df.empty:
            df["macro_vix"] = pd.Series(dtype=float)
            df["sp500_200ma_up"] = pd.Series(dtype=bool)
            df["kospi_200ma_up"] = pd.Series(dtype=bool)
            df["macro_warning"] = pd.Series(dtype=object)
            return df
            
        as_of_date = context.as_of_date
        
        # yfinance로 VIX와 S&P500(^GSPC) 데이터 가져오기
        # 200일 이평선을 구하기 위해 1년(약 252 거래일) 이상의 데이터를 가져옵니다.
        # 주의: as_of_date 기준으로 미래 데이터를 쓰지 않도록 종료일을 as_of_date 직후로 잡습니다.
        try:
            end_date = pd.to_datetime(as_of_date) + pd.Timedelta(days=1)
            start_date = end_date - pd.Timedelta(days=400)
            
            # VIX
            vix_ticker = yf.Ticker("^VIX")
            vix_hist = vix_ticker.history(start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"))
            current_vix = vix_hist['Close'].iloc[-1] if not vix_hist.empty else 0.0
            
            # S&P 500
            sp500_ticker = yf.Ticker("^GSPC")
            sp500_hist = sp500_ticker.history(start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"))
            sp500_up = False
            if not sp500_hist.empty and len(sp500_hist) >= 200:
                sp500_close = sp500_hist['Close'].iloc[-1]
                sp500_200ma = sp500_hist['Close'].iloc[-200:].mean()
                sp500_up = sp500_close > sp500_200ma
                
            # KOSPI (context.krx_client 사용)
            kospi_up = False
            if context.krx_client:
                # 069500 (KODEX 200)로 추세 확인
                k_start = (pd.to_datetime(as_of_date) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
                k_hist = context.krx_client.get_ohlcv_batch(["069500"], k_start, as_of_date).get("069500")
                if k_hist is not None and not k_hist.empty and len(k_hist) >= 200:
                    kospi_close = k_hist['close'].iloc[-1]
                    kospi_200ma = k_hist['close'].iloc[-200:].mean()
                    kospi_up = kospi_close > kospi_200ma
                    
            macro_warning = None
            if current_vix > algo_settings.vix_hard_block:
                macro_warning = "현재 거시 위험도 극상으로 시스템이 신규 DCA 매수를 일시 차단합니다"
                
            df["macro_vix"] = current_vix
            df["sp500_200ma_up"] = sp500_up
            df["kospi_200ma_up"] = kospi_up
            df["macro_warning"] = macro_warning
            
        except Exception as e:
            logger.warning(f"MacroFilterNode 데이터 수집 실패: {e}")
            df["macro_vix"]       = None   # 0.0(정상)과 구별: None=미수집
            df["sp500_200ma_up"]  = None
            df["kospi_200ma_up"]  = None
            df["macro_warning"]   = f"거시 데이터 수집 실패 ({type(e).__name__})"
            
        return df
