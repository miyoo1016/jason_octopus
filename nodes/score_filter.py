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
            
            # [개선] 수급 질적 필터링 (퍼플렉시티 제안: 역방향 수급 패널티)
            # 외인/기관 둘 다 양수일 때만 고득점(15점 초과) 허용
            if f_flow > 0 and i_flow > 0:
                flow = min(30.0, f_flow + i_flow)
            else:
                # 어느 한쪽이 팔고 있거나 수급이 없으면 최대 15점으로 제한
                flow = min(15.0, f_flow + i_flow)
            
            # [추가] 섹터 강세 보너스 (+5점)
            sector_bonus = 0
            if "✅" in str(row.get("sector_strength_label", "")):
                sector_bonus = 5
            
            total = int(vcp + breakout_score + rs + flow + sector_bonus)
            total = min(total, 210) # 210점 만점 캡 적용
            
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
            
            # [최종 완성형 Tier 판정] - 퍼플렉시티의 '점수 기반 가중치' 제안 반영
            # 총점을 기반으로 등급을 나누되, 핵심 지표(RS, 수급)가 부족하면 하향 조정
            tier = 3
            rs_val = float(row.get("rs_rating", 0)) if not pd.isna(row.get("rs_rating", 0)) else 0.0
            vcp_warn = str(row.get("vcp_warning", ""))
            flow_score = float(row.get("flow_score", 0)) + float(row.get("institution_flow_score", 0))
            
            # 1. 총점 기반 기본 티어
            if total >= 190:
                tier = 1
            elif total >= 150:
                tier = 2
            else:
                tier = 3
                
            # 2. 실력(RS) 보강 - 총점이 낮더라도 RS가 압도적인 초주도주면 Tier 2 보장
            if rs_val >= 95 and tier > 2:
                tier = 2

            # 3. 주도주 검증 (강등 로직: 실력 없는 종목이 우연히 점수만 높을 때)
            if tier == 1:
                # RS가 85 미만이거나 수급이 10 미만이면 Tier 2로 강등 (최소한의 리더십 검증)
                if rs_val < 85 or flow_score < 10:
                    tier = 2
                # 패턴에 치명적 결함(미완성)이 있으면 강등
                if "미완성" in vcp_warn:
                    tier = 2
                    
            tiers.append(tier)
                
        res_df["tier"] = tiers
        return res_df
