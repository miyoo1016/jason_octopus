import pandas as pd
import pytest
from nodes.score_filter import ScoreFilterNode, ScoreFilterParams
from engine.node_base import ExecutionContext

@pytest.fixture
def ctx():
    return ExecutionContext(as_of_date="2026-05-09", run_id="refine_test")

def test_refined_rejection_logic(ctx):
    node = ScoreFilterNode()
    
    # mock 종목 A: RS 95, VCP REVERSE_EXPANSION, breakout_status IN_BOX, 수급 15, ma_alignment ALIGNED
    # -> Tier 1/2는 아니지만 REJECTED가 아니라 WATCHLIST 또는 TIER_3
    df_a = pd.DataFrame([{
        "code": "A", "rs_rating": 95, "vcp_status": "REVERSE_EXPANSION", "breakout_status": "IN_BOX",
        "flow_total_score": 15, "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID",
        "vcp_score": 50, "breakout_score": 10, "rs_score": 50, "macro_score": 50
    }])
    out_a = node.run([df_a], ScoreFilterParams(), ctx)
    assert out_a.loc[0, "primary_bucket"] == "WATCHLIST"
    assert "Risk Watch" in out_a.loc[0, "tier_reason"]

    # mock 종목 B: RS 95, VCP REVERSE_EXPANSION, NOT_READY, 수급 5, ma_alignment NOT_ALIGNED
    # -> REJECTED (복합 약점: 4개 이상)
    df_b = pd.DataFrame([{
        "code": "B", "rs_rating": 95, "vcp_status": "REVERSE_EXPANSION", "breakout_status": "NOT_READY",
        "flow_total_score": 5, "ma_alignment_flag": "NOT_ALIGNED", "liquidity_status": "LIQUID",
        "breakout_distance_pct": 20.0,
        "foreign_net_buy": -200000, "institution_net_buy": -200000,
        "vcp_score": 20, "breakout_score": 5, "rs_score": 50, "macro_score": 50
    }])
    out_b = node.run([df_b], ScoreFilterParams(), ctx)
    assert out_b.loc[0, "primary_bucket"] == "REJECTED"

    # mock 종목 C: RS 80 이상, 역수축 경고 단독
    # -> WATCHLIST / Risk Watch
    df_c = pd.DataFrame([{
        "code": "C", "rs_rating": 85, "vcp_status": "VCP_VALID", "vcp_warning": "역수축 경고",
        "breakout_status": "IN_BOX", "flow_total_score": 20, "ma_alignment_flag": "ALIGNED",
        "vcp_score": 70, "breakout_score": 10, "rs_score": 40, "macro_score": 50
    }])
    out_c = node.run([df_c], ScoreFilterParams(), ctx)
    assert out_c.loc[0, "primary_bucket"] == "WATCHLIST"
    assert "Risk Watch" in out_c.loc[0, "tier_reason"]

    # mock 종목 D: RS 45, NOT_READY, 수급 5
    # -> REJECTED
    df_d = pd.DataFrame([{
        "code": "D", "rs_rating": 45, "vcp_status": "NOT_READY", "breakout_status": "IN_BOX",
        "flow_total_score": 5, "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID",
        "vcp_score": 30, "breakout_score": 10, "rs_score": 10, "macro_score": 50
    }])
    out_d = node.run([df_d], ScoreFilterParams(), ctx)
    assert out_d.loc[0, "primary_bucket"] == "REJECTED"

    # mock 종목 E: 수급점수 30, 박스 상단 근접, RS 55, VCP_WARNING
    # -> WATCHLIST 또는 TIER_3
    df_e = pd.DataFrame([{
        "code": "E", "rs_rating": 55, "vcp_status": "VCP_WARNING", "breakout_status": "IN_BOX",
        "breakout_distance_pct": 2.0, "flow_total_score": 30, "ma_alignment_flag": "ALIGNED",
        "vcp_score": 60, "breakout_score": 10, "rs_score": 20, "macro_score": 50
    }])
    out_e = node.run([df_e], ScoreFilterParams(), ctx)
    assert out_e.loc[0, "primary_bucket"] in ["WATCHLIST", "TIER_3"]
