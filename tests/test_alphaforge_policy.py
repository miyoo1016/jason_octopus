import pytest
import pandas as pd
from backend.alphaforge_policy import (
    classify_primary_bucket, compute_candidate_confidence,
    normalize_vcp_score, classify_watch_alert, check_policy_invariants
)

# Fixtures based on features (Samsung, Hynix, Shinhan analogs)

@pytest.fixture
def action_leader_candidate():
    """Analogous to Hanmi Semiconductor"""
    return {
        "code": "C1", "name": "Leader",
        "rs_percentile": 86.4,
        "vcp_status": "VCP_WARNING",
        "breakout_status": "IN_BOX",
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "flow_total_score": 25.0,
        "total_score": 160,
        "breakout_distance_pct": 2.0
    }

@pytest.fixture
def high_rs_reverse_expansion():
    """Analogous to SK Hynix / Samsung Electronics"""
    return {
        "code": "C2", "name": "Overextended",
        "rs_percentile": 95.8,
        "vcp_status": "REVERSE_EXPANSION",
        "breakout_status": "FAILED_BREAKOUT",
        "data_unit_check": "DATA_UNIT_WARNING",
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "flow_total_score": 20.0,
        "total_score": 150
    }

@pytest.fixture
def low_rs_high_vcp():
    """Analogous to Shinhan Financial Group"""
    return {
        "code": "C3", "name": "Anomaly",
        "rs_percentile": 26.3,
        "vcp_score": 78,
        "vcp_status": "VCP_VALID",
        "ma_alignment_flag": "NOT_ALIGNED",
        "liquidity_status": "LIQUID",
        "flow_total_score": 10.0,
        "total_score": 90,
        "primary_bucket": "REJECTED"
    }

def test_action_alert_policy(action_leader_candidate):
    row = action_leader_candidate
    bucket, _, _, _, _ = classify_primary_bucket(row)
    row["primary_bucket"] = bucket
    assert bucket == "TIER_2"
    
    alert_flag, alert_type, action_flag, _, _, _ = classify_watch_alert(row)
    assert alert_flag is True
    assert alert_type == "ACTION_ALERT"
    assert action_flag is True

def test_hynix_style_risk_watch(high_rs_reverse_expansion):
    row = high_rs_reverse_expansion
    bucket, _, _, _, _ = classify_primary_bucket(row)
    row["primary_bucket"] = bucket
    # Should be WATCHLIST or TIER depending on rules
    
    alert_flag, alert_type, action_flag, _, _, _ = classify_watch_alert(row)
    assert alert_flag is True
    assert alert_type in ["RISK_WATCH", "DATA_REVIEW"]
    assert action_flag is False

def test_shinhan_style_anomaly(low_rs_high_vcp):
    row = low_rs_high_vcp
    # Shinhan is low RS and ma not aligned, should be REJECTED
    bucket, _, _, _, _ = classify_primary_bucket(row)
    row["primary_bucket"] = bucket
    assert bucket == "REJECTED"
    
    # VCP score normalization
    raw, eff, disp, conf, cross = normalize_vcp_score(row)
    assert raw == 78
    assert eff <= 45
    assert conf == "CROSS_FACTOR_WEAK"
    assert cross == "HIGH_VCP_REJECTED_BY_HARD_GATE"
    
    # Alert
    alert_flag, _, _, _, _, _ = classify_watch_alert(row)
    assert alert_flag is False

def test_invariants_generic():
    # Test Invariant 2: REVERSE_EXPANSION cannot have Action Alert
    row = {
        "primary_bucket": "TIER_2",
        "vcp_status": "REVERSE_EXPANSION",
        "action_alert_flag": True,
        "watch_alert_flag": True,
        "watch_alert_type": "ACTION_ALERT",
        "watch_alert_reasons": ["FOO"]
    }
    violations = check_policy_invariants(row)
    assert any("Invariant 2" in v for v in violations)

def test_invariants_rejected():
    # Test Invariant 1: REJECTED cannot have Alert
    row = {
        "primary_bucket": "REJECTED",
        "watch_alert_flag": True,
        "watch_alert_reasons": ["FOO"]
    }
    violations = check_policy_invariants(row)
    assert any("Invariant 1" in v for v in violations)
