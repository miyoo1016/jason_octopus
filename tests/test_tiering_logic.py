import pandas as pd
import pytest
from nodes.score_filter import ScoreFilterNode, ScoreFilterParams
from engine.node_base import ExecutionContext

def test_tier2_promotion_leadership_candidate():
    node = ScoreFilterNode()
    ctx = ExecutionContext(as_of_date="2026-05-09", run_id="test")
    
    # Mock data: RS 90, Liquid, Aligned, IN_BOX, dist 5.0 (within 7%), VCP_WARNING
    df = pd.DataFrame([{
        "code": "000001",
        "name": "PromotedT2",
        "total_score": 150,
        "rs_rating": 90,
        "rs_status": "Strong",
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "vcp_status": "VCP_WARNING",
        "vcp_warning": "거래량 미감소",
        "breakout_status": "IN_BOX",
        "breakout_distance_pct": 5.0,
        "flow_total_score": 15.0,
        "has_flow": True,
        "box_breakout_warning": "거래량 부족"
    }])
    
    out = node.run([df], ScoreFilterParams(), ctx)
    assert out.loc[0, "primary_bucket"] == "TIER_2"
    assert "RS 리더십 후보" in out.loc[0, "promotion_reasons"]
    assert "정배열 + 박스권 허용 범위" in out.loc[0, "promotion_reasons"]

def test_tier2_rejection_deep_box():
    node = ScoreFilterNode()
    ctx = ExecutionContext(as_of_date="2026-05-09", run_id="test")
    
    # RS 90 but box distance 10.0 (> 7.0) -> Rejection from T2
    df = pd.DataFrame([{
        "code": "000002",
        "name": "DeepBoxT2Rejected",
        "total_score": 140,
        "rs_rating": 90,
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "vcp_status": "VCP_VALID",
        "breakout_status": "IN_BOX",
        "breakout_distance_pct": 10.0,
        "flow_total_score": 15.0,
        "has_flow": True
    }])
    
    out = node.run([df], ScoreFilterParams(), ctx)
    assert out.loc[0, "primary_bucket"] != "TIER_2"
    assert "박스권 깊음" in out.loc[0, "t2_rejection_reasons"]

def test_tier2_rejection_reverse_expansion():
    node = ScoreFilterNode()
    ctx = ExecutionContext(as_of_date="2026-05-09", run_id="test")
    
    # RS 95 but REVERSE_EXPANSION -> Rejection from T2
    df = pd.DataFrame([{
        "code": "000003",
        "name": "RevExpT2Rejected",
        "total_score": 140,
        "rs_rating": 95,
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "vcp_status": "REVERSE_EXPANSION",
        "breakout_status": "IN_BOX",
        "breakout_distance_pct": 3.0,
        "flow_total_score": 15.0,
        "has_flow": True
    }])
    
    out = node.run([df], ScoreFilterParams(), ctx)
    assert out.loc[0, "primary_bucket"] != "TIER_2"
    assert "VCP 역수축" in out.loc[0, "t2_rejection_reasons"]

def test_tier2_rejection_low_flow():
    node = ScoreFilterNode()
    ctx = ExecutionContext(as_of_date="2026-05-09", run_id="test")
    
    # RS 85 but Flow score 0 -> Rejection from T2
    df = pd.DataFrame([{
        "code": "000004",
        "name": "LowFlowT2Rejected",
        "total_score": 120,
        "rs_rating": 85,
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "vcp_status": "VCP_VALID",
        "breakout_status": "IN_BOX",
        "breakout_distance_pct": 5.0,
        "flow_score": 0.0,
        "institution_flow_score": 0.0,
        "foreign_net_buy": -200000, # This will trigger the 5.0 cap or similar
        "institution_net_buy": -200000,
        "has_flow": True
    }])
    
    out = node.run([df], ScoreFilterParams(), ctx)
    assert out.loc[0, "primary_bucket"] != "TIER_2"
    assert "수급 약함" in out.loc[0, "t2_rejection_reasons"]

def test_rs_low_rejection():
    node = ScoreFilterNode()
    ctx = ExecutionContext(as_of_date="2026-05-09", run_id="test")
    
    # RS 40 -> Never T2
    df = pd.DataFrame([{
        "code": "000004",
        "name": "LowRS",
        "total_score": 140,
        "rs_rating": 40,
        "rs_status": "LOW_RS",
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "vcp_status": "VCP_VALID",
        "breakout_status": "NEAR_BREAKOUT",
        "breakout_distance_pct": 2.0,
        "flow_total_score": 20.0,
        "has_flow": True
    }])
    
    out = node.run([df], ScoreFilterParams(), ctx)
    assert out.loc[0, "primary_bucket"] not in {"TIER_1", "TIER_2"}

def test_primary_bucket_alignment():
    node = ScoreFilterNode()
    ctx = ExecutionContext(as_of_date="2026-05-09", run_id="test")
    
    # 2 rows in, 2 rows out with primary_bucket
    df = pd.DataFrame([
        {"code": "A", "total_score": 180, "rs_rating": 95, "vcp_status": "VCP_STRICT", "breakout_status": "BREAKOUT_CONFIRMED", "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID", "has_flow": True, "flow_total_score": 30},
        {"code": "B", "total_score": 50, "rs_rating": 30, "vcp_status": "NOT_READY", "breakout_status": "NOT_READY", "ma_alignment_flag": "NOT_ALIGNED", "liquidity_status": "LOW", "has_flow": False, "flow_total_score": 0},
    ])
    
    out = node.run([df], ScoreFilterParams(), ctx)
    assert len(out) == 2
    assert out["primary_bucket"].isin(["TIER_1", "TIER_2", "TIER_3", "WATCHLIST", "REJECTED"]).all()

def test_watchlist_flag_quality_rules():
    node = ScoreFilterNode()
    ctx = ExecutionContext(as_of_date="2026-05-09", run_id="test")
    
    # Mock A: TIER_2 -> True
    df_a = pd.DataFrame([{
        "code": "A", "name": "A", "rs_rating": 90, "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID", "vcp_status": "VCP_STRICT", "breakout_status": "NEAR_BREAKOUT",
        "breakout_distance_pct": 2.0, "flow_total_score": 15.0, "total_score": 160
    }])
    
    # Mock B: TIER_3, but high quality -> True
    df_b = pd.DataFrame([{
        "code": "B", "name": "B", "rs_rating": 85, "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID", "vcp_status": "VCP_VALID", "breakout_status": "IN_BOX",
        "breakout_distance_pct": 5.0, "flow_total_score": 15.0, "total_score": 140
    }])
    
    # Mock C: TIER_3, but low quality (RS 40, REV_EXP) -> False
    df_c = pd.DataFrame([{
        "code": "C", "name": "C", "rs_rating": 40, "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID", "vcp_status": "REVERSE_EXPANSION", "breakout_status": "IN_BOX",
        "breakout_distance_pct": 3.0, "flow_total_score": 10.0, "total_score": 110
    }])
    
    # Mock D: WATCHLIST, but very low quality -> False
    df_d = pd.DataFrame([{
        "code": "D", "name": "D", "rs_rating": 30, "ma_alignment_flag": "NOT_ALIGNED",
        "liquidity_status": "LIQUID", "vcp_status": "NOT_READY", "breakout_status": "NOT_READY",
        "breakout_distance_pct": 15.0, "flow_total_score": 5.0, "total_score": 60
    }])
    
    # Mock E: WATCHLIST, but quality signal (NEAR_BREAKOUT, RS 75) -> True
    df_e = pd.DataFrame([{
        "code": "E", "name": "E", "rs_rating": 75, "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID", "vcp_status": "VCP_VALID", "breakout_status": "NEAR_BREAKOUT",
        "breakout_distance_pct": 2.0, "flow_total_score": 15.0, "total_score": 90
    }])
    
    out_a = node.run([df_a], ScoreFilterParams(), ctx)
    assert out_a.loc[0, "watchlist_flag"] == True
    
    out_b = node.run([df_b], ScoreFilterParams(), ctx)
    assert out_b.loc[0, "watchlist_flag"] == True
    
    out_c = node.run([df_c], ScoreFilterParams(), ctx)
    assert out_c.loc[0, "watchlist_flag"] == False
    
    out_d = node.run([df_d], ScoreFilterParams(), ctx)
    assert out_d.loc[0, "watchlist_flag"] == False
    
    out_e = node.run([df_e], ScoreFilterParams(), ctx)
    assert out_e.loc[0, "watchlist_flag"] == True

def test_watchlist_flag_not_all_true():
    node = ScoreFilterNode()
    ctx = ExecutionContext(as_of_date="2026-05-09", run_id="test")
    
    df = pd.DataFrame([
        {"code": "1", "name": "G", "rs_rating": 90, "ma_alignment_flag": "ALIGNED", "vcp_status": "VCP_STRICT", "breakout_status": "NEAR_BREAKOUT", "total_score": 160},
        {"code": "2", "name": "B", "rs_rating": 30, "ma_alignment_flag": "NOT_ALIGNED", "vcp_status": "NOT_READY", "breakout_status": "NOT_READY", "total_score": 50}
    ])
    
    out = node.run([df], ScoreFilterParams(), ctx)
    assert out["watchlist_flag"].sum() < len(out)
