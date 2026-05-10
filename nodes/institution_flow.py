"""
기관 수급 노드.
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from nodes.foreign_flow import validate_flow_integrity

class InstitutionFlowParams(BaseModel):
    n_days: int = 5

class InstitutionFlowNode(BaseNode):
    NODE_TYPE      = "institution_flow"
    DISPLAY_NAME   = "기관 수급"
    DESCRIPTION    = "기관 N일 누적 순매수 데이터를 추가합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("institution_net_buy", "institution_flow_score", "institution_flow_warning", "institution_flow_flag")
    ParamsModel    = InstitutionFlowParams

    def run(self, inputs: list[pd.DataFrame], params: InstitutionFlowParams, context: ExecutionContext) -> pd.DataFrame:
        if not context.krx_client:
            raise RuntimeError("krx_client가 제공되지 않았습니다.")
        df = inputs[0]
        if df.empty:
            df["institution_net_buy"] = pd.Series(dtype=int)
            df["institution_flow_score"] = pd.Series(dtype=int)
            df["institution_flow_flag"] = pd.Series(dtype=object)
            return df

        result = context.krx_client.get_institution_flow(df, context.as_of_date, n_days=params.n_days)

        from backend.algo_settings import algo_settings
        flow_hist = result.attrs.get("institution_flow_hist", {})
        scores = []
        warnings = []
        adjusted_net_buy = []
        flags = []

        short_days = algo_settings.flow_days_short
        mid_days = algo_settings.flow_days_mid
        long_days = algo_settings.flow_days_long

        for _, row in result.iterrows():
            code = row["code"]
            hist = flow_hist.get(code, [])
            net_buy = row.get("institution_net_buy", 0)

            score = 0
            warning = None

            if pd.isna(net_buy) or not hist:
                # [개선] 데이터 공백 시 최근 N일 평균으로 추정
                if hist:
                    avg_flow = sum(r[2] for r in hist) / len(hist)
                    warning = f"기관 수급 당일 데이터 누락 (최근 {len(hist)}일 평균 {avg_flow:,.0f}주로 대체)"
                    net_buy = avg_flow
                    flags.append("ESTIMATED")
                else:
                    scores.append(50)
                    warnings.append("기관 수급 데이터 없음")
                    adjusted_net_buy.append(pd.NA)
                    flags.append("DATA_MISSING")
                    continue
            else:
                flags.append("AVAILABLE")

            # 1. 단기 순매수 확인 (5점)
            short_flow = sum(r[2] for r in hist[:short_days])
            if short_flow > 0:
                score += 5
                # 대량 매집 보너스
                if short_flow > 500_000: score += 5
                if short_flow > 2_000_000: score += 10

            # 2. 중기 매집 확인 (10점)
            mid_flow = sum(r[2] for r in hist[:mid_days])
            if mid_flow > 0:
                score += 5
                if mid_flow > 3_000_000: score += 5

            # 3. 장기 추세 (5점)
            if len(hist) >= long_days:
                recent = sum(r[2] for r in hist[:long_days//2])
                past = sum(r[2] for r in hist[long_days//2:long_days])
                if recent > past:
                    score += 5

            # [추가] 연속 매수성 확인
            consecutive_buys = 0
            for r in hist[:5]: # 최근 5거래일 확인
                if r[2] > 0:
                    consecutive_buys += 1
                else:
                    break

            if consecutive_buys >= 3: score += 5
            if consecutive_buys >= 5: score += 5

            scores.append(min(30, score))
            warnings.append(warning)
            adjusted_net_buy.append(net_buy)

        if len(adjusted_net_buy) == len(result):
            result["institution_net_buy"] = pd.to_numeric(
                pd.Series(adjusted_net_buy), errors="coerce"
            ).round().astype("Int64")
        result["institution_flow_score"] = scores
        result["institution_flow_warning"] = warnings
        result["institution_flow_flag"] = flags
        for _, row in result.iterrows():
            validate_flow_integrity(row, "institution_net_buy", "institution_flow_score")
        return result
