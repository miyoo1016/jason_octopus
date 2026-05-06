"""
OR 결합 노드 — 2개 이상의 입력을 합집합으로 결합합니다.
"""
import pandas as pd
from engine.node_base import BaseNode, ExecutionContext, EmptyParams

class OrFilterNode(BaseNode):
    NODE_TYPE      = "or_filter"
    DISPLAY_NAME   = "OR 결합"
    DESCRIPTION    = "모든 입력의 합집합 종목을 합칩니다."
    INPUT_ARITY    = 2   # 최소 2, 3개 이상도 가능
    OUTPUT_COLUMNS = ()
    ParamsModel    = EmptyParams

    def run(self, inputs: list[pd.DataFrame], params: EmptyParams, context: ExecutionContext) -> pd.DataFrame:
        valid = [df for df in inputs if not df.empty and "code" in df.columns]
        if not valid:
            return pd.DataFrame()
        if len(valid) == 1:
            return valid[0]

        # 합집합 + 컬럼 병합: 중복 코드는 두 입력의 컬럼을 모두 보존
        merged = valid[0].copy()
        for other in valid[1:]:
            new_code_mask = ~other["code"].isin(merged["code"])
            new_cols = [c for c in other.columns if c not in merged.columns and c != "code"]

            # 기존 코드에 새 컬럼 추가 (left join)
            if new_cols:
                existing = other[~new_code_mask][["code"] + new_cols]
                merged = merged.merge(existing, on="code", how="left")

            # 신규 코드 행 추가
            if new_code_mask.any():
                merged = pd.concat([merged, other[new_code_mask]], ignore_index=True)

        return merged.reset_index(drop=True)
