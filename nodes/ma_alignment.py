"""
이평선 정배열 노드 (5 > 20 > 60).
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from engine.leakage_guard import assert_no_future_data
from data.holidays import prev_trading_day

class MaAlignmentParams(BaseModel):
    pass

class MaAlignmentNode(BaseNode):
    NODE_TYPE      = "ma_alignment"
    DISPLAY_NAME   = "이평선 정배열 찾기"
    DESCRIPTION    = "단기(5) > 중기(20) > 장기(60) 이동평균선 정배열 종목 필터."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ()
    ParamsModel    = MaAlignmentParams

    def run(self, inputs: list[pd.DataFrame], params: MaAlignmentParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0]
        if df.empty or not context.krx_client:
            return df
            
        start_date = prev_trading_day(context.as_of_date, n=90) # 충분한 영업일 확보 (60 거래일)
        codes = df["code"].tolist()
        
        # OHLCV 배치 조회
        ohlcv_dict = context.krx_client.get_ohlcv_batch(codes, start_date, context.as_of_date)
        
        valid_codes = set()
        for code, hist in ohlcv_dict.items():
            if hist.empty or len(hist) < 60:
                continue
            assert_no_future_data(hist, context.as_of_date, context=f"MaAlignmentNode:{code}")

            # 종가 기준 이평선 계산
            hist["MA5"] = hist["close"].rolling(window=5).mean()
            hist["MA20"] = hist["close"].rolling(window=20).mean()
            hist["MA60"] = hist["close"].rolling(window=60).mean()
            
            last_row = hist.iloc[-1]
            ma5 = last_row["MA5"]
            ma20 = last_row["MA20"]
            ma60 = last_row["MA60"]
            
            if pd.isna(ma5) or pd.isna(ma20) or pd.isna(ma60):
                continue
                
            if ma5 > ma20 > ma60:
                valid_codes.add(code)
                
        result = df[df["code"].isin(valid_codes)].reset_index(drop=True)
        return result
