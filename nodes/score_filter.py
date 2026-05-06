"""
점수 임계값 필터 노드.
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext

class ScoreFilterParams(BaseModel):
    pass

class ScoreFilterNode(BaseNode):
    NODE_TYPE      = "score_filter"
    DISPLAY_NAME   = "최종 점수 종합"
    DESCRIPTION    = "모든 지표를 합산하여 총점과 Tier를 산출합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("total_score", "tier")
    ParamsModel    = ScoreFilterParams

    def run(self, inputs: list[pd.DataFrame], params: ScoreFilterParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0].copy()
        if df.empty:
            df["total_score"] = pd.Series(dtype=int)
            df["tier"] = pd.Series(dtype=int)
            return df
            
        results = []
        for _, row in df.iterrows():
            vcp = float(row.get("vcp_score", 0)) if not pd.isna(row.get("vcp_score", 0)) else 0.0
            vcp = min(vcp, 100.0) # 최대 100점
            
            grade = str(row.get("box_breakout_grade", "D"))
            if grade.startswith("A"):
                breakout_score = 50
            elif grade.startswith("B"):
                breakout_score = 30
            elif grade.startswith("C"):
                breakout_score = 10
            else:
                breakout_score = 0
            
            rs = float(row.get("rs_score", 0)) if not pd.isna(row.get("rs_score", 0)) else 0.0
            
            f_flow = float(row.get("flow_score", 0)) if not pd.isna(row.get("flow_score", 0)) else 0.0
            i_flow = float(row.get("institution_flow_score", 0)) if not pd.isna(row.get("institution_flow_score", 0)) else 0.0
            flow = min(30.0, f_flow + i_flow) # 외국인/기관 수급 합산 (최대 30점)
            
            total = int(vcp + breakout_score + rs + flow)
            total = min(total, 210) # 210점 만점 캡 적용 (오버플로우 방지)
            
            row_dict = row.to_dict()
            row_dict["total_score"] = total
            results.append(row_dict)
            
        res_df = pd.DataFrame(results)
        
        # 2. Tier 산출 (점수 내림차순 정렬)
        res_df = res_df.sort_values("total_score", ascending=False).reset_index(drop=True)
        total_count = len(res_df)
        
        tiers = []
        for idx, row in res_df.iterrows():
            total = row.get("total_score", 0)
            pct_rank = (idx + 1) / total_count if total_count > 0 else 1.0
            
            base_tier = 3
            # RS 90점 이상(최상위 주도주)은 기본적으로 Tier 2 보장
            rs_val = float(row.get("rs_rating", 0)) if not pd.isna(row.get("rs_rating", 0)) else 0.0
            
            if total >= 160 and pct_rank <= 0.25:
                base_tier = 1
            elif (total >= 150 and pct_rank <= 0.60) or rs_val >= 90:
                base_tier = 2
                
            # VCP 경고 및 돌파 D등급에 대한 페널티 완화 적용
            # 단, RS 95점 이상의 초주도주는 페널티 면제 (추세 강도가 기술적 결함을 압도함)
            grade = row.get("box_breakout_grade", "D")
            vcp_warn = str(row.get("vcp_warning", ""))
            
            penalty = 0
            if rs_val < 95:
                if "미완성" in vcp_warn or "미감소" in vcp_warn:
                    penalty += 1
                if grade == "D":
                    penalty += 1
                
            final_tier = min(3, base_tier + penalty)
            tiers.append(final_tier)
                
        res_df["tier"] = tiers
        return res_df
