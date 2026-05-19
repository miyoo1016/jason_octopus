import pandas as pd

from backend.alphaforge_policy import (
    clean_display_reasons,
    get_display_rejected_reasons,
    get_buy_candidate_gate_result,
    infer_display_watch_alert_type,
    infer_final_label,
    normalize_reason_label,
    normalize_result_schema,
)
from engine.node_base import ExecutionContext
from nodes.score_filter import ScoreFilterNode, ScoreFilterParams


def test_rejected_reasons_from_classification_text():
    row = normalize_result_schema({
        "primary_bucket": "REJECTED",
        "tier_reason": "REJECTED: [복합 약점] RS 50 미만, 이평선 비정렬",
        "restriction_reasons": ["RS 80 미달", "이평선 비정렬"],
    })

    assert row["display_rejected_reasons"] == ["RS 50 미만", "이평선 비정렬"]
    assert row["promotion_reasons"] == []
    assert row["display_promotion_reasons"] == []


def test_rejected_reasons_feature_fallback():
    reasons = get_display_rejected_reasons({
        "primary_bucket": "REJECTED",
        "rs_percentile": 26.3,
        "ma_alignment": "NOT_ALIGNED",
    })

    assert "RS 50 미만" in reasons
    assert "이평선 비정렬" in reasons


def test_watch_alert_type_display_and_fallback():
    assert infer_display_watch_alert_type({
        "watch_alert_flag": True,
        "watch_alert_type": "RISK_WATCH",
    }) == "RISK_WATCH"
    assert infer_display_watch_alert_type({
        "watch_alert_flag": True,
        "vcp_status": "REVERSE_EXPANSION",
    }) == "RISK_WATCH"
    assert infer_display_watch_alert_type({
        "primary_bucket": "TIER_3",
        "watch_alert_flag": True,
        "watch_alert_type": "ACTION_ALERT",
    }) == "PRIORITY_WATCH"
    assert infer_display_watch_alert_type({
        "primary_bucket": "TIER_2",
        "watch_alert_flag": True,
        "watch_alert_type": "ACTION_ALERT",
    }) == "NEAR_BUY"


def _buy_gate_candidate(**overrides):
    row = {
        "primary_bucket": "TIER_2",
        "watch_alert_flag": True,
        "watch_alert_type": "ACTION_ALERT",
        "rs_percentile": 86,
        "vcp_effective_score": 68,
        "vcp_status": "VCP_VALID",
        "ma_alignment_flag": "ALIGNED",
        "box_depth": 12,
        "breakout_status": "BREAKOUT_CONFIRMED",
        "breakout_volume_ratio": 1.6,
        "liquidity_trading_value": 3_000_000_000,
        "dominant_regime": "NEUTRAL",
        "data_unit_check": "OK",
        "liquidity_status": "LIQUID",
    }
    row.update(overrides)
    return row


def _buy_gate_context(mode="STRICT_MODE"):
    return {
        "screening_mode": mode,
        "min_trading_value_krw": 2_000_000_000,
        "market_regime": {"dominant_regime": "NEUTRAL"},
    }


def test_explore_mode_never_creates_buy_candidate():
    row = _buy_gate_candidate()
    row.update(get_buy_candidate_gate_result(row, _buy_gate_context("EXPLORE_MODE")))

    assert row["buy_gate_passed"] is False
    assert "SCREENING_MODE_NOT_STRICT" in row["failed_buy_gates"]
    assert infer_final_label(row) == "NEAR_BUY"


def test_strict_mode_all_gates_pass_creates_buy_candidate():
    row = _buy_gate_candidate()
    row.update(get_buy_candidate_gate_result(row, _buy_gate_context("STRICT_MODE")))

    assert row["buy_gate_passed"] is True
    assert row["failed_buy_gates"] == []
    assert infer_final_label(row) == "BUY_CANDIDATE"


def test_reverse_expansion_blocks_buy_candidate():
    row = _buy_gate_candidate(vcp_status="REVERSE_EXPANSION")
    row.update(get_buy_candidate_gate_result(row, _buy_gate_context("STRICT_MODE")))

    assert row["buy_gate_passed"] is False
    assert "VCP_REVERSE_EXPANSION" in row["failed_buy_gates"]
    assert infer_final_label(row) != "BUY_CANDIDATE"


def test_low_vcp_blocks_buy_and_records_failed_gate():
    row = _buy_gate_candidate(vcp_effective_score=55)
    row.update(get_buy_candidate_gate_result(row, _buy_gate_context("STRICT_MODE")))

    assert row["buy_gate_passed"] is False
    assert row["failed_buy_gates"] == ["VCP_SCORE_BELOW_60"]
    assert row["buy_gate_reason"] == "VCP_SCORE_BELOW_60"
    assert infer_final_label(row) == "NEAR_BUY"


def test_reason_code_korean_labels():
    labels = [
        normalize_reason_label("HIGH_RS_CANDIDATE"),
        normalize_reason_label("DATA_REVIEW_REQUIRED"),
        normalize_reason_label("RISK_WATCH_REQUIRED"),
    ]

    assert labels == ["고RS 후보", "데이터 확인 필요", "리스크 관찰 필요"]


def test_duplicate_display_reason_cleanup():
    reasons = clean_display_reasons(
        ["관찰 후보", "주요 구조 안정적", "구조적 안정성", "강한 RS 리더십", "RS 리더십 후보"],
        "TIER_3",
    )

    assert reasons == ["관찰 후보", "구조적 안정성", "강한 RS 리더십"]


def test_score_filter_preserves_vcp_diagnostic_fields():
    df = pd.DataFrame([{
        "code": "VCP001",
        "name": "VCP진단",
        "total_score": 120,
        "score_max": 210,
        "rs_percentile": 85,
        "rs_rating": 85,
        "rs_score": 85,
        "rs_status": "Strong",
        "vcp_score": 78,
        "vcp_raw_score": 78,
        "vcp_status": "VCP_WARNING",
        "breakout_score": 15,
        "breakout_status": "IN_BOX",
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "flow_total_score": 20,
        "breakout_distance_pct": 4,
    }])

    out = ScoreFilterNode().run([df], ScoreFilterParams(), ExecutionContext(as_of_date="2026-05-10", run_id="test"))
    row = out.iloc[0]

    assert "vcp_raw_score" in out.columns
    assert "vcp_effective_score" in out.columns
    assert "vcp_display_score" in out.columns
    assert row["vcp_raw_score"] == 78
    assert "raw" in row["vcp_diagnostic"]
