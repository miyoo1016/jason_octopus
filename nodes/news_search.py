"""
뉴스 검색 노드. (Perplexity 등 외부 API 연동용)
현재는 모의(dummy) 구현입니다.
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext

class NewsSearchParams(BaseModel):
    query_template: str = "{name} 최근 특징주 주가 전망"
    max_news_count: int = 3

class NewsSearchNode(BaseNode):
    NODE_TYPE      = "news_search"
    DISPLAY_NAME   = "뉴스 검색"
    DESCRIPTION    = "Perplexity 또는 일반 뉴스 검색 결과를 종목에 추가합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("recent_news",)
    ParamsModel    = NewsSearchParams

    def run(self, inputs: list[pd.DataFrame], params: NewsSearchParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0].copy()
        if df.empty:
            return df
            
        # 임시(Mock) 구현: 실제 환경에서는 여기서 Perplexity API 등을 호출합니다.
        # 시간/비용 문제상 현재는 모의 텍스트 삽입
        news_list = []
        for _, row in df.iterrows():
            name = row.get("name", "알 수 없음")
            dummy_news = f"[{name}] 외국인 대량 매수세 유입 등 긍정적 전망"
            news_list.append(dummy_news)
            
        df["recent_news"] = news_list
        return df
