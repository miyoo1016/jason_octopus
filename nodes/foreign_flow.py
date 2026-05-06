"""
외국인 수급 노드.
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext

class ForeignFlowParams(BaseModel):
    n_days: int = 5

class ForeignFlowNode(BaseNode):
    NODE_TYPE      = "foreign_flow"
    DISPLAY_NAME   = "외국인 수급"
    DESCRIPTION    = "외국인 N일 누적 순매수 데이터를 추가합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("foreign_net_buy", "flow_score")
    ParamsModel    = ForeignFlowParams

    def run(self, inputs: list[pd.DataFrame], params: ForeignFlowParams, context: ExecutionContext) -> pd.DataFrame:
        if not context.krx_client:
            raise RuntimeError("krx_client가 제공되지 않았습니다.")
        df = inputs[0]
        if df.empty:
            df["foreign_net_buy"] = pd.Series(dtype=int)
            df["flow_score"] = pd.Series(dtype=int)
            return df
            
        result = context.krx_client.get_foreign_flow(df, context.as_of_date, n_days=params.n_days)
        
        # 장기 수급 로직 적용
        from backend.algo_settings import algo_settings
        
        flow_hist = result.attrs.get("foreign_flow_hist", {})
        scores = []
        
        mid_days = algo_settings.flow_days_mid
        long_days = algo_settings.flow_days_long
        
        short_days = algo_settings.flow_days_short

        for _, row in result.iterrows():
            code = row["code"]
            hist = flow_hist.get(code, [])
            net_buy = row.get("foreign_net_buy", 0)

            score = 0
            if not hist:
                scores.append(0)
                continue

            # 1. 단기 순매수 존재 확인 (5점)
            short_flow = sum(r[1] for r in hist[:short_days])
            if short_flow > 0:
                score += 5
                
                # [개선] 순매수 강도 보너스 (370만 주 같은 대량 매집 우대)
                # 최근 20일 평균 거래량 대비 순매수 비중 등 고려 (간소화하여 절대 수량 기준 보너스)
                if short_flow > 1_000_000: score += 5
                if short_flow > 3_000_000: score += 10

            # 2. 중기 매집 확인 (15점)
            mid_flow = sum(r[1] for r in hist[:mid_days])
            if mid_flow > 0:
                score += 10
                if mid_flow > 5_000_000: score += 5

            # 3. 장기 추세 우상향 (5점)
            if len(hist) >= long_days:
                long_flow_recent = sum(r[1] for r in hist[:long_days//2])
                long_flow_past = sum(r[1] for r in hist[long_days//2:long_days])
                if long_flow_recent > long_flow_past:
                    score += 5

            scores.append(min(30, score))
            
        result["flow_score"] = scores
        return result
