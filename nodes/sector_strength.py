"""
섹터 강도(Sector Strength) 분석 노드.
각 섹터별 대표 ETF의 수익률을 기반으로 섹터의 강세를 판별합니다.
"""
import pandas as pd
import logging
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from data.holidays import prev_trading_day

logger = logging.getLogger(__name__)

class SectorStrengthParams(BaseModel):
    lookback_days: int = 20

class SectorStrengthNode(BaseNode):
    NODE_TYPE      = "sector_strength"
    DISPLAY_NAME   = "섹터 강도 분석"
    DESCRIPTION    = "섹터별 대표 ETF의 수익률을 기반으로 강세 여부 판별."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("sector_strength", "sector_strength_label")
    ParamsModel    = SectorStrengthParams

    # 섹터-ETF 매핑 테이블
    SECTOR_ETF_MAP = {
        "반도체와반도체장비": "091160", # KODEX 반도체
        "증권": "102960",            # KODEX 증권
        "전기장비": "381180",          # TIGER 에너지인프라
        "전자장비와기기": "091160",     # Fallback (유관 섹터)
        "복합기업": "069500",          # KOSPI 200
        "반도체ETF": "091160",
        "지수레버리지ETF": "069500",
    }
    FALLBACK_ETF = "069500" # KOSPI 200

    def run(self, inputs: list[pd.DataFrame], params: SectorStrengthParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0]
        if df.empty or not context.krx_client:
            return df

        as_of = context.as_of_date
        start_date = prev_trading_day(as_of, n=params.lookback_days)
        
        # 1. 고유 ETF 리스트 추출
        etf_codes = list(set(self.SECTOR_ETF_MAP.values())) + [self.FALLBACK_ETF]
        
        # 2. ETF 가격 수집
        logger.info("섹터 강도 계산: ETF %d종목 수집 (%s ~ %s)", len(etf_codes), start_date, as_of)
        ohlcv_dict = context.krx_client.get_ohlcv_batch(etf_codes, start_date=start_date, end_date=as_of)
        
        # 3. ETF별 수익률 계산
        etf_returns = {}
        for code, hist in ohlcv_dict.items():
            if not hist.empty and len(hist) >= 2:
                ret = (hist["close"].iloc[-1] / hist["close"].iloc[0]) - 1.0
                etf_returns[code] = ret
        
        # 4. 종목별 섹터 강도 매핑
        strengths = []
        labels = []
        
        for _, row in df.iterrows():
            sector = row.get("sector", "기타")
            etf_code = self.SECTOR_ETF_MAP.get(sector, self.FALLBACK_ETF)
            ret = etf_returns.get(etf_code, 0.0)
            
            strengths.append(round(ret * 100, 2))
            if ret >= 0:
                labels.append("섹터 강세 ✅")
            else:
                labels.append("섹터 약세 ⚠️")
                
        result = df.copy()
        result["sector_strength"] = strengths
        result["sector_strength_label"] = labels
        return result
