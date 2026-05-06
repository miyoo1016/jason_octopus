"""
상위 N개 선택 노드.
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext

class TopNParams(BaseModel):
    sort_column: str = "market_cap"
    ascending: bool = False
    n: int = 50

class TopNNode(BaseNode):
    NODE_TYPE      = "top_n"
    DISPLAY_NAME   = "Top N 필터"
    DESCRIPTION    = "정렬 후 상위 N개 종목만 선택합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ()
    ParamsModel    = TopNParams

    def run(self, inputs: list[pd.DataFrame], params: TopNParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0]
        if df.empty or params.sort_column not in df.columns:
            return df
            
        result = df.sort_values(by=params.sort_column, ascending=params.ascending)
        result = result.head(params.n).reset_index(drop=True)
        return result
