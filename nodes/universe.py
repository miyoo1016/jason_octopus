"""
국장 종목 유니버스 노드.
"""
from typing import Literal
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext

class UniverseParams(BaseModel):
    market: Literal["ALL", "KOSPI", "KOSDAQ"] = "ALL"
    manual_codes: list[str] = None  # 특정 종목만 분석할 때 사용

class UniverseNode(BaseNode):
    NODE_TYPE      = "universe"
    DISPLAY_NAME   = "국장 종목"
    DESCRIPTION    = "KOSPI / KOSDAQ 전 종목을 불러옵니다."
    INPUT_ARITY    = 0
    OUTPUT_COLUMNS = ("market_cap",)
    ParamsModel    = UniverseParams

    def run(self, inputs: list[pd.DataFrame], params: UniverseParams, context: ExecutionContext) -> pd.DataFrame:
        if not context.krx_client:
            raise RuntimeError("krx_client가 제공되지 않았습니다.")
        
        df = context.krx_client.get_universe(context.as_of_date, market=params.market)
        
        # 특정 종목 코드만 요청된 경우 필터링
        if params.manual_codes:
            df = df[df["code"].isin(params.manual_codes)].reset_index(drop=True)
            
        return df
