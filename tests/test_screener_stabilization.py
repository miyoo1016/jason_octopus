import pandas as pd
import pytest
from nodes.vcp import VcpNode, VcpParams
from nodes.box_breakout import BoxBreakoutNode, BoxBreakoutParams
from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
from nodes.score_filter import ScoreFilterNode, ScoreFilterParams
from engine.node_base import ExecutionContext

@pytest.fixture
def ctx():
    return ExecutionContext(as_of_date="2026-05-09", run_id="stabilization_test")

def test_no_row_dropping_policy(ctx):
    node_vcp = VcpNode()
    node_box = BoxBreakoutNode()
    
    df_box = pd.DataFrame([{"code": "005930", "name": "삼성전자"}])
    out_box = node_box.run([df_box], BoxBreakoutParams(), ctx)
    assert len(out_box) == len(df_box)
    
    df_vcp = pd.DataFrame([{"code": "005930", "name": "삼성전자"}])
    out_vcp = node_vcp.run([df_vcp], VcpParams(), ctx)
    assert len(out_vcp) == len(df_vcp)

def test_primary_bucket_mutual_exclusivity(ctx):
    node = ScoreFilterNode()
    df = pd.DataFrame([
        # Code 1: Perfect Tier 1
        {
            "code": "1", "rs_rating": 95, "rs_score": 50, "ma_alignment_flag": "ALIGNED", 
            "liquidity_status": "LIQUID", "vcp_status": "VCP_STRICT", "vcp_score": 98,
            "breakout_status": "BREAKOUT_CONFIRMED", "breakout_score": 30, "breakout_distance_pct": 0.0,
            "flow_total_score": 30, "macro_score": 60, "sector_strength_label": "✅"
        },
        # Code 2: RS low Rejected
        {
            "code": "2", "rs_rating": 40, "rs_score": 10, "vcp_status": "REVERSE_EXPANSION", "vcp_score": 35,
            "total_score": 80
        },
        # Code 3: Tier 2 (IN_BOX)
        {
            "code": "3", "rs_rating": 85, "rs_score": 35, "ma_alignment_flag": "ALIGNED", 
            "liquidity_status": "LIQUID", "vcp_status": "VCP_VALID", "vcp_score": 78,
            "breakout_status": "IN_BOX", "breakout_score": 15, "breakout_distance_pct": 5.0,
            "flow_total_score": 15, "macro_score": 50
        }
    ])
    
    out = node.run([df], ScoreFilterParams(), ctx)
    assert len(out) == 3
    
    row1 = out[out["code"] == "1"].iloc[0]
    row2 = out[out["code"] == "2"].iloc[0]
    row3 = out[out["code"] == "3"].iloc[0]
    
    assert row1["primary_bucket"] == "TIER_1"
    assert row2["primary_bucket"] == "REJECTED"
    assert row3["primary_bucket"] == "TIER_2"

def test_tier_rejection_rules(ctx):
    node = ScoreFilterNode()
    
    # 1. REVERSE_EXPANSION -> Never T2 or above
    df_rev = pd.DataFrame([{
        "code": "REV", "rs_rating": 95, "rs_score": 50, "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID",
        "vcp_status": "REVERSE_EXPANSION", "vcp_score": 35, "breakout_status": "BREAKOUT_CONFIRMED", "breakout_score": 30, 
        "breakout_distance_pct": 0.0, "flow_total_score": 15
    }])
    out_rev = node.run([df_rev], ScoreFilterParams(), ctx)
    assert out_rev.loc[0, "primary_bucket"] == "WATCHLIST"
    assert "Risk Watch" in out_rev.loc[0, "tier_reason"]

def test_nan_diagnostics_exclusion(ctx):
    df = pd.DataFrame({
        "rs_score": [100, None],
        "vcp_warning": ["OK", None],
        "tier_reason": ["Good", None]
    })
    
    from backend.analysis_summary import _top_nan_columns
    nans = _top_nan_columns(df)
    cols = [item["column"] for item in nans]
    assert "rs_score" in cols
    assert "vcp_warning" not in cols
    assert "tier_reason" not in cols

def test_watchlist_flag_differentiation(ctx):
    node = ScoreFilterNode()
    df = pd.DataFrame([
        # High quality Tier 3
        {
            "code": "T3_GOOD", "rs_rating": 85, "rs_score": 35, "ma_alignment_flag": "ALIGNED", 
            "liquidity_status": "LIQUID", "vcp_status": "VCP_VALID", "vcp_score": 78,
            "breakout_status": "IN_BOX", "breakout_score": 15, "breakout_distance_pct": 8.0,
            "flow_total_score": 15, "macro_score": 50
        },
        # Low quality Watchlist
        {
            "code": "WL_BAD", "rs_rating": 40, "rs_score": 10, "vcp_status": "NOT_READY", "vcp_score": 35,
            "breakout_status": "NOT_READY", "breakout_score": 5
        }
    ])
    out = node.run([df], ScoreFilterParams(), ctx)
    
    row_t3 = out[out["code"] == "T3_GOOD"].iloc[0]
    row_wl = out[out["code"] == "WL_BAD"].iloc[0]
    
    assert row_t3["watchlist_flag"] == True
    assert row_wl["watchlist_flag"] == False
