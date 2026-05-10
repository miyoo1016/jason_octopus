import pytest
import pandas as pd
from nodes.score_filter import ScoreFilterNode
from engine.node_base import ExecutionContext

@pytest.fixture
def score_node():
    return ScoreFilterNode()

def test_sk_hynix_case(score_node):
    # SK하이닉스: RS 95.8, REVERSE_EXPANSION, FAILED_BREAKOUT, DATA_UNIT_WARNING
    df = pd.DataFrame([{
        "code": "000660",
        "name": "SK하이닉스",
        "rs_percentile": 95.8,
        "rs_rating": 95.8,
        "vcp_status": "REVERSE_EXPANSION",
        "breakout_status": "FAILED_BREAKOUT",
        "data_unit_check": "DATA_UNIT_WARNING",
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "flow_total_score": 20,
        "total_score": 150
    }])
    
    ctx = ExecutionContext(as_of_date="2026-05-10", run_id="test_run")
    res = score_node.run([df], None, ctx)
    
    row = res.iloc[0]
    # Choice B: 초고RS(90+)는 알림 허용 (Action Alert는 아니지만 관찰 알림)
    assert bool(row["watchlist_flag"]) is True
    assert row["watch_alert_type"] == "DATA_REVIEW"
    # Exclusion reasons에 차단 사유들 포함 확인
    exclusions = row["watch_alert_exclusion_reasons"]
    assert "FAILED_BREAKOUT_BLOCK" in exclusions
    assert "REVERSE_EXPANSION_BLOCK" in exclusions
    assert "DATA_UNIT_WARNING_ACTION_BLOCK" in exclusions
    # Decision trace에 전환 기록 확인
    assert "Action 차단되나 초고RS로 Data Review 전환" in row["watch_alert_decision_trace"]

def test_samsung_elec_mech_case(score_node):
    # 삼성전기: RS 92.7, VCP_WARNING, DATA_UNIT_WARNING
    df = pd.DataFrame([{
        "code": "009150",
        "name": "삼성전기",
        "rs_percentile": 92.7,
        "rs_rating": 92.7,
        "vcp_status": "VCP_WARNING",
        "breakout_status": "IN_BOX",
        "data_unit_check": "DATA_UNIT_WARNING",
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "flow_total_score": 15,
        "total_score": 130
    }])
    
    ctx = ExecutionContext(as_of_date="2026-05-10", run_id="test_run")
    res = score_node.run([df], None, ctx)
    
    row = res.iloc[0]
    # ACTION_ALERT 금지, DATA_REVIEW 또는 SETUP_WATCH 허용
    assert row["watch_alert_type"] in ["DATA_REVIEW", "SETUP_WATCH"]
    assert "DATA_UNIT_WARNING_ACTION_BLOCK" in row["watch_alert_exclusion_reasons"]

def test_shinhan_vcp_anomaly(score_node):
    # 신한지주: RS 26.3, REJECTED, VCP 78
    df = pd.DataFrame([{
        "code": "055550",
        "name": "신한지주",
        "rs_percentile": 26.3,
        "rs_rating": 26.3,
        "vcp_score": 78,
        "vcp_status": "VCP_VALID",
        "ma_alignment_flag": "NOT_ALIGNED",
        "liquidity_status": "LIQUID",
        "flow_total_score": 10,
        "total_score": 90
    }])
    
    ctx = ExecutionContext(as_of_date="2026-05-10", run_id="test_run")
    res = score_node.run([df], None, ctx)
    
    row = res.iloc[0]
    # Rejected 확인
    assert row["primary_bucket"] == "REJECTED"
    # Cross warning 확인
    assert row["vcp_cross_warning"] == "HIGH_VCP_REJECTED_BY_HARD_GATE"
    # Candidate confidence는 LOW / WEAK / VERY_LOW (REJECTED + CROSS_FACTOR_WEAK)
    assert row["candidate_confidence"] in ["LOW", "WEAK", "VERY_LOW"]

def test_hanmi_leadership(score_node):
    # 한미반도체: Tier 2, RS 86.4
    df = pd.DataFrame([{
        "code": "042700",
        "name": "한미반도체",
        "rs_percentile": 86.4,
        "rs_rating": 86.4,
        "vcp_status": "VCP_WARNING",
        "breakout_status": "IN_BOX",
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "flow_total_score": 25,
        "total_score": 160
    }])
    
    ctx = ExecutionContext(as_of_date="2026-05-10", run_id="test_run")
    res = score_node.run([df], None, ctx)
    
    row = res.iloc[0]
    # Tier 2 확인
    assert row["primary_bucket"] == "TIER_2"
    # Action Alert 확인
    assert row["watch_alert_type"] == "ACTION_ALERT"
    assert "TIER_2_LEADERSHIP" in row["watch_alert_reasons"]
