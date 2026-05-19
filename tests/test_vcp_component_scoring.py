from backend.alphaforge_policy import get_buy_candidate_gate_result
from nodes.vcp import build_vcp_component_scores, classify_vcp_component_status


def test_component_vcp_confirmed_for_clear_contraction():
    score, components, reason = build_vcp_component_scores(
        contraction_count=3,
        max_contraction=8.0,
        adjusted_box_limit=15.0,
        recent_volatility=0.07,
        dist_from_high=0.025,
        volume_declining=True,
        volume_expanding=False,
        volume_dryup_ratio=0.55,
        width_trend="CONTRACTING",
        contraction_lows=[90.0, 94.0, 97.0],
    )

    assert score >= 75
    assert classify_vcp_component_status(score) == "VCP_CONFIRMED"
    assert components["contraction_count_score"] > 0
    assert "수축" in reason


def test_component_vcp_reverse_expansion_has_priority():
    score, components, _ = build_vcp_component_scores(
        contraction_count=3,
        max_contraction=18.0,
        adjusted_box_limit=15.0,
        recent_volatility=0.18,
        dist_from_high=0.06,
        volume_declining=False,
        volume_expanding=True,
        volume_dryup_ratio=1.35,
        width_trend="EXPANDING",
        contraction_lows=[96.0, 92.0, 88.0],
        is_reverse=True,
    )

    assert score <= 44
    assert components["reverse_expansion_penalty"] > 0
    assert classify_vcp_component_status(score, is_reverse=True) == "REVERSE_EXPANSION"


def test_component_vcp_without_volume_dryup_is_limited():
    score, components, _ = build_vcp_component_scores(
        contraction_count=3,
        max_contraction=9.0,
        adjusted_box_limit=15.0,
        recent_volatility=0.08,
        dist_from_high=0.04,
        volume_declining=False,
        volume_expanding=True,
        volume_dryup_ratio=1.30,
        width_trend="CONTRACTING",
        contraction_lows=[90.0, 94.0, 97.0],
    )

    assert components["volume_dry_up_score"] == 0
    assert score < 75


def test_component_vcp_wide_box_is_limited():
    score, components, _ = build_vcp_component_scores(
        contraction_count=3,
        max_contraction=26.0,
        adjusted_box_limit=15.0,
        recent_volatility=0.12,
        dist_from_high=0.04,
        volume_declining=True,
        volume_expanding=False,
        volume_dryup_ratio=0.65,
        width_trend="STABLE",
        contraction_lows=[90.0, 92.0, 93.0],
    )

    assert components["box_tightness_score"] == 0
    assert score < 75


def test_buy_gate_still_uses_effective_vcp_60_threshold():
    candidate = {
        "rs_percentile": 90,
        "vcp_effective_score": 59,
        "vcp_status": "VCP_FORMING",
        "ma_alignment_flag": "ALIGNED",
        "breakout_distance_pct": 2.0,
        "breakout_status": "NEAR_BREAKOUT",
        "liquidity_trading_value": 10_000_000_000,
        "data_unit_check": "OK",
    }
    gate = get_buy_candidate_gate_result(
        candidate,
        {
            "screening_mode": "STRICT_MODE",
            "min_trading_value_krw": 2_000_000_000,
            "market_regime": {"dominant_regime": "NEUTRAL"},
        },
    )

    assert gate["buy_gate_passed"] is False
    assert "VCP_SCORE_BELOW_60" in gate["failed_buy_gates"]
