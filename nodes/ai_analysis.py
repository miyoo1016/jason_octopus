"""
Gemini LLM 분석 노드.
"""
from typing import Any
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from llm.gemini import gemini_analyze_stocks

class AiAnalysisParams(BaseModel):
    max_tokens_per_stock: int = 256
    system_prompt: str = ""

class AiAnalysisNode(BaseNode):
    NODE_TYPE      = "ai_analysis"
    DISPLAY_NAME   = "AI 분석 (Gemini)"
    DESCRIPTION    = "종목 목록을 Gemini Flash 모델에 전달하여 코멘트와 점수를 받습니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("score", "grade", "comment")
    ParamsModel    = AiAnalysisParams

    def run(self, inputs: list[pd.DataFrame], params: AiAnalysisParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0]
        if df.empty:
            return df

        # DataFrame을 딕셔너리 리스트로 변환
        stocks = df.to_dict(orient="records")
        
        system_prompt = params.system_prompt if params.system_prompt else None
        
        # 분석 수행
        analyzed_stocks, usages = gemini_analyze_stocks(
            stocks=stocks,
            system_prompt=system_prompt,
            max_tokens_per_stock=params.max_tokens_per_stock,
        )
        
        # 다시 DataFrame으로 변환
        result_df = pd.DataFrame(analyzed_stocks)
        return result_df
