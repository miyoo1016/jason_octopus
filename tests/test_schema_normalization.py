import pytest
import pandas as pd
import numpy as np
from backend.alphaforge_policy import normalize_result_schema

def test_normalize_result_schema_basic():
    row = {"symbol": "005930", "name": "삼성전자"}
    normalized = normalize_result_schema(row)
    
    assert "tier_promotion_reasons" in normalized
    assert isinstance(normalized["tier_promotion_reasons"], list)
    assert normalized["primary_bucket"] == "REJECTED" # Default
    assert normalized["vcp_score"] == 0.0

def test_normalize_result_schema_mixed_types():
    row = {
        "tier_promotion_reasons": "강한 주도주",
        "rejected_reasons": None,
        "risk_gate_reasons": pd.NA,
        "watch_alert_reasons": ["Reason 1", None],
        "symbol": "123456"
    }
    normalized = normalize_result_schema(row)
    
    assert normalized["tier_promotion_reasons"] == ["강한 주도주"]
    assert normalized["rejected_reasons"] == []
    assert normalized["risk_gate_reasons"] == []
    assert normalized["watch_alert_reasons"] == ["Reason 1"]

def test_normalize_result_schema_numpy():
    row = {
        "promotion_reasons": np.array(["A", "B"]),
        "symbol": "000001"
    }
    normalized = normalize_result_schema(row)
    assert normalized["promotion_reasons"] == ["A", "B"]
    assert normalized["promotion_reasons_str"] == "A; B"

def test_normalize_result_schema_score_max():
    # Case 1: 명시적 score_max는 보존
    row = {"total_score": 125, "score_max": 250, "symbol": "S1"}
    assert normalize_result_schema(row)["score_max"] == 250.0

    # Case 2: total_score > 100 → 210 (default)
    row = {"total_score": 145, "symbol": "S2"}
    assert normalize_result_schema(row)["score_max"] == 210.0

    # Case 3: total_score <= 100 → 여전히 210 (run-level constant)
    # 이전 버그: 95 → score_max=100으로 추정해 95/100으로 표시되던 회귀 방지
    row = {"total_score": 85, "symbol": "S3"}
    assert normalize_result_schema(row)["score_max"] == 210.0

    # Case 4: total_score=95 (가장 빈번한 회귀 케이스) → 95/210
    row = {"total_score": 95, "symbol": "S4"}
    normalized = normalize_result_schema(row)
    assert normalized["score_max"] == 210.0
    assert normalized["score_pct"] == round(95 / 210 * 100, 2)

    # Case 5: run-level override
    row = {"total_score": 50, "symbol": "S5"}
    normalized = normalize_result_schema(row, run_context={"score_max": 100.0})
    assert normalized["score_max"] == 100.0

def test_normalize_result_schema_display_reasons():
    row = {
        "primary_bucket": "WATCHLIST",
        "watchlist_reasons": ["수급 대기"],
        "promotion_reasons": ["강한 주도주"],
        "symbol": "S4"
    }
    normalized = normalize_result_schema(row)
    assert normalized["display_promotion_reasons"] == ["수급 대기"]
    
    row["primary_bucket"] = "TIER_1"
    normalized = normalize_result_schema(row)
    assert normalized["display_promotion_reasons"] == ["강한 주도주"]

if __name__ == "__main__":
    pytest.main([__file__])
