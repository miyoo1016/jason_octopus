"""
AND 결합 노드 — 2개 이상의 입력을 교집합으로 결합합니다.
"""
import pandas as pd
from engine.node_base import BaseNode, ExecutionContext, EmptyParams

class AndFilterNode(BaseNode):
    NODE_TYPE      = "and_filter"
    DISPLAY_NAME   = "AND 결합"
    DESCRIPTION    = "모든 입력의 교집합 종목만 남깁니다."
    INPUT_ARITY    = 2   # 최소 2, 3개 이상도 가능
    OUTPUT_COLUMNS = ()
    ParamsModel    = EmptyParams

    def run(self, inputs: list[pd.DataFrame], params: EmptyParams, context: ExecutionContext) -> pd.DataFrame:
        # 유효한 입력만 수집
        valid = [df for df in inputs if not df.empty and "code" in df.columns]
        if not valid:
            return pd.DataFrame()
        if len(valid) == 1:
            return valid[0]

        # 첫 번째 기준, 나머지와 교집합
        result = valid[0]
        for other in valid[1:]:
            common = set(result["code"]) & set(other["code"])
            
            # [수정] 정밀 분석 모드에서 교집합이 깨질 경우 (한쪽 노드 데이터 누락 등) 합집합으로 폴백
            if not common and context.is_single_analysis:
                import logging
                logging.getLogger(__name__).warning("AndFilterNode: 정밀 분석 중 교집합 공집합 발생 -> 합집합 폴백")
                result = pd.concat([result, other], ignore_index=True).drop_duplicates("code")
            else:
                result = result[result["code"].isin(common)].reset_index(drop=True)
        return result
