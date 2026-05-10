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
    DESCRIPTION    = "단기(5) > 중기(20) > 장기(60) 이동평균선 정렬 상태를 표시합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("ma_alignment_flag", "ma_alignment_score")
    ParamsModel    = MaAlignmentParams

    def run(self, inputs: list[pd.DataFrame], params: MaAlignmentParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0]
        if df.empty or not context.krx_client:
            return df
            
        start_date = prev_trading_day(context.as_of_date, n=90) # 충분한 영업일 확보 (60 거래일)
        codes = df["code"].tolist()
        
        # OHLCV 배치 조회
        ohlcv_dict = context.krx_client.get_ohlcv_batch(codes, start_date, context.as_of_date)
        
        results = []
        for _, row in df.iterrows():
            code = row["code"]
            hist = ohlcv_dict.get(code)
            row_dict = row.to_dict()
            if hist is None or hist.empty or len(hist) < 60:
                # [FIX] DATA_MISSING은 판단 보류 — 50점 가산 금지 (None 처리)
                row_dict["ma_alignment_flag"] = "DATA_MISSING"
                row_dict["ma_alignment_score"] = None
                row_dict["ma_alignment_warning"] = (
                    f"이평선 가격 데이터 부족 (OHLCV {0 if hist is None else len(hist)}개 < 60개)"
                )
                results.append(row_dict)
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
            
            is_aligned = (not pd.isna(ma5) and not pd.isna(ma20) and not pd.isna(ma60) and ma5 > ma20 > ma60)
            
            row_dict["ma_alignment_flag"] = "ALIGNED" if is_aligned else "NOT_ALIGNED"
            row_dict["ma_alignment_score"] = 50 if is_aligned else 20
            if not is_aligned:
                row_dict["ma_alignment_warning"] = "이평선 정배열 아님"
            results.append(row_dict)

        return pd.DataFrame(results)
