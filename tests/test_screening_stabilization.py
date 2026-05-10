import pytest
import pandas as pd
from nodes.score_filter import ScoreFilterNode
from engine.node_base import ExecutionContext

@pytest.fixture
def score_filter_node():
    return ScoreFilterNode()

@pytest.fixture
def base_df():
    # 기본 데이터 프레임 생성
    data = {
        "code": ["000001", "000002", "000003", "000004", "000005"],
        "name": ["A", "B", "C", "D", "E"],
        "rs_rating": [90.0, 85.0, 75.0, 40.0, 95.0],
        "rs_score": [60, 40, 40, 10, 60],
        "rs_status": ["Strong", "Strong", "Strong", "Low", "Strong"],
        "vcp_score": [95, 65, 50, 35, 35],
        "vcp_status": ["VCP_STRICT", "VCP_WARNING", "BASE_BUILDING", "NOT_READY", "REVERSE_EXPANSION"],
        "vcp_warning": ["", "변동성 높음", "", "", "역수축 경고"],
        "breakout_score": [30, 24, 24, 5, 30],
        "breakout_status": ["BREAKOUT_CONFIRMED", "NEAR_BREAKOUT", "NEAR_BREAKOUT", "NOT_READY", "BREAKOUT_CONFIRMED"],
        "ma_alignment_flag": ["ALIGNED", "ALIGNED", "ALIGNED", "NOT_ALIGNED", "ALIGNED"],
        "liquidity_status": ["LIQUID", "LIQUID", "LIQUID", "LIQUID", "LIQUID"],
        "breakout_distance_pct": [-1.0, 5.0, -4.0, -20.0, -1.0],
        "flow_score": [15, 10, 15, 5, 15],
        "institution_flow_score": [15, 10, 15, 5, 15],
        "foreign_net_buy": [1000, 500, 1000, -500, 1000],
        "institution_net_buy": [1000, 500, 1000, -500, 1000],
        "box_breakout_warning": ["", "", "", "", ""],
    }
    return pd.DataFrame(data)

def test_tier_1_strict_criteria(score_filter_node, base_df):
    # A 종목은 Tier 1 조건을 모두 만족함
    context = ExecutionContext(as_of_date="2024-05-09", run_id="test_run")
    result = score_filter_node.run([base_df], None, context)
    
    a_row = result[result["code"] == "000001"].iloc[0]
    assert a_row["primary_bucket"] == "TIER_1"

def test_tier_2_strong_leader_logic(score_filter_node, base_df):
    # B 종목은 RS 우수, NEAR_BREAKOUT, VCP_VALID 등 Tier 2 조건 만족
    context = ExecutionContext(as_of_date="2024-05-09", run_id="test_run")
    result = score_filter_node.run([base_df], None, context)
    
    b_row = result[result["code"] == "000002"].iloc[0]
    assert b_row["primary_bucket"] == "TIER_2"

def test_reverse_expansion_watchlist_retention(score_filter_node, base_df):
    # E 종목은 REVERSE_EXPANSION이지만 RS 95로 강력함 -> Rejected가 아니라 WATCHLIST 또는 TIER_3
    context = ExecutionContext(as_of_date="2024-05-09", run_id="test_run")
    result = score_filter_node.run([base_df], None, context)
    
    e_row = result[result["code"] == "000005"].iloc[0]
    assert e_row["primary_bucket"] in ["WATCHLIST", "TIER_3"]
    assert "역수축" in e_row["tier_reason"]

def test_rejected_multiple_weaknesses(score_filter_node, base_df):
    # D 종목은 RS 저조, NOT_READY, 수급 약함 등 복합 약점 -> REJECTED
    context = ExecutionContext(as_of_date="2024-05-09", run_id="test_run")
    result = score_filter_node.run([base_df], None, context)
    
    d_row = result[result["code"] == "000004"].iloc[0]
    assert d_row["primary_bucket"] == "REJECTED"

def test_primary_total_count_matches_universe(score_filter_node, base_df):
    context = ExecutionContext(as_of_date="2024-05-09", run_id="test_run")
    result = score_filter_node.run([base_df], None, context)
    assert len(result) == len(base_df)

def test_watchlist_flag_narrower_than_bucket(score_filter_node, base_df):
    context = ExecutionContext(as_of_date="2024-05-09", run_id="test_run")
    result = score_filter_node.run([base_df], None, context)
    
    # Tier 1, 2는 기본 True
    assert result[result["primary_bucket"] == "TIER_1"]["watchlist_flag"].all()
    assert result[result["primary_bucket"] == "TIER_2"]["watchlist_flag"].all()
    
    # Rejected는 False
    assert not result[result["primary_bucket"] == "REJECTED"]["watchlist_flag"].any()
