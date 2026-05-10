"""
외국인 수급 노드.
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext


def validate_flow_integrity(row: pd.Series, value_col: str, score_col: str) -> None:
    """수급 숫자와 점수의 기본 정합성을 콘솔 로그로 점검합니다."""
    value = row.get(value_col)
    score = row.get(score_col)
    if pd.isna(value):
        return
    if float(value) == 0 and not pd.isna(score) and float(score) != 0:
        code = row.get("code", "")
        name = row.get("name", "")
        print(f"[WARN] 수급 정합성 불일치: {code} {name} {value_col}=0, {score_col}={score}")

class ForeignFlowParams(BaseModel):
    n_days: int = 5

class ForeignFlowNode(BaseNode):
    NODE_TYPE      = "foreign_flow"
    DISPLAY_NAME   = "외국인 수급"
    DESCRIPTION    = "외국인 N일 누적 순매수 데이터를 추가합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("foreign_net_buy", "flow_score", "foreign_flow_flag", "foreign_flow_warning")
    ParamsModel    = ForeignFlowParams

    def run(self, inputs: list[pd.DataFrame], params: ForeignFlowParams, context: ExecutionContext) -> pd.DataFrame:
        if not context.krx_client:
            raise RuntimeError("krx_client가 제공되지 않았습니다.")
        df = inputs[0]
        if df.empty:
            df["foreign_net_buy"] = pd.Series(dtype=int)
            df["flow_score"] = pd.Series(dtype=int)
            df["foreign_flow_flag"] = pd.Series(dtype=object)
            df["foreign_flow_warning"] = pd.Series(dtype=object)
            return df
            
        result = context.krx_client.get_foreign_flow(df, context.as_of_date, n_days=params.n_days)
        
        # 장기 수급 로직 적용
        from backend.algo_settings import algo_settings
        
        flow_hist = result.attrs.get("foreign_flow_hist", {})
        scores = []
        flags = []
        warnings = []
        
        mid_days = algo_settings.flow_days_mid
        long_days = algo_settings.flow_days_long
        
        short_days = algo_settings.flow_days_short

        for _, row in result.iterrows():
            code = row["code"]
            hist = flow_hist.get(code, [])
            net_buy = row.get("foreign_net_buy", 0)

            score = 0
            warning = None
            if pd.isna(net_buy) or not hist:
                if hist:
                    # [개선] 데이터 공백 시 최근 N일 평균으로 추정
                    avg_flow = sum(r[1] for r in hist) / len(hist)
                    warning = f"외국인 수급 당일 데이터 누락 (최근 {len(hist)}일 평균 {avg_flow:,.0f}주로 대체)"
                    net_buy = avg_flow
                    flags.append("ESTIMATED")
                else:
                    scores.append(50)
                    flags.append("DATA_MISSING")
                    warnings.append("외국인 수급 데이터 없음")
                    continue
            else:
                flags.append("AVAILABLE")

            # 1. 단기 순매수 존재 확인 (5점)
            short_flow = sum(r[1] for r in hist[:short_days])
            if short_flow > 0:
                score += 5
                
                # [개선] 순매수 강도 보너스 (370만 주 같은 대량 매집 우대)
                if short_flow > 1_000_000: score += 5
                if short_flow > 3_000_000: score += 10

            # 2. 중기 매집 확인 (10점)
            mid_flow = sum(r[1] for r in hist[:mid_days])
            if mid_flow > 0:
                score += 5 # 상향 조정 (합계 30점 만점 유지)
                if mid_flow > 5_000_000: score += 5

            # 3. 장기 추세 우상향 (5점)
            if len(hist) >= long_days:
                long_flow_recent = sum(r[1] for r in hist[:long_days//2])
                long_flow_past = sum(r[1] for r in hist[long_days//2:long_days])
                if long_flow_recent > long_flow_past:
                    score += 5

            # [추가] 연속 매수성 확인
            consecutive_buys = 0
            for r in hist[:5]: # 최근 5거래일 확인
                if r[1] > 0:
                    consecutive_buys += 1
                else:
                    break
            
            if consecutive_buys >= 3: score += 5
            if consecutive_buys >= 5: score += 5

            scores.append(min(30, score))
            warnings.append(warning)

        result["flow_score"] = scores
        result["foreign_flow_flag"] = flags
        result["foreign_flow_warning"] = warnings
        for _, row in result.iterrows():
            validate_flow_integrity(row, "foreign_net_buy", "flow_score")
        return result
