from types import SimpleNamespace

import pandas as pd

from backend.analysis_summary import build_analysis_payload
from backend.market_regime import calculate_market_regime
from engine.node_base import ExecutionContext
from nodes.score_filter import ScoreFilterNode, ScoreFilterParams


def _strong_candidate(regime="NEUTRAL", total_score=180):
    return pd.DataFrame([{
        "code": "000001",
        "name": "Strong",
        "market": "KOSPI",
        "close": 1000,
        "volume": 100000,
        "market_cap": 1_000_000_000_000,
        "rs_rating": 92,
        "rs_score": 50,
        "rs_status": "Strong",
        "ma_alignment_flag": "ALIGNED",
        "liquidity_status": "LIQUID",
        "vcp_status": "VCP_STRICT",
        "vcp_score": 98,
        "breakout_status": "NEAR_BREAKOUT",
        "breakout_score": 30,
        "breakout_distance_pct": 2.0,
        "flow_total_score": 20,
        "macro_score": 50,
        "dominant_regime": regime,
        "secondary_regime": "NEUTRAL",
        "risk_on_prob": 10,
        "neutral_prob": 20,
        "risk_off_prob": 60 if regime == "RISK_OFF" else 20,
        "crisis_prob": 70 if regime == "CRISIS" else 10,
        "regime_data_status": "지연",
        "regime_as_of": "2026-05-10 16:00",
        "regime_data_sources": ["test"],
        "total_score": total_score,
    }])


def test_market_regime_distribution_sums_to_100():
    regime = calculate_market_regime(vix=17, sp500_up=True, kospi_up=True, as_of_date="2026-05-10")
    total = regime["risk_on_prob"] + regime["neutral_prob"] + regime["risk_off_prob"] + regime["crisis_prob"]
    assert total == 100
    assert regime["dominant_regime"] in {"RISK_ON", "NEUTRAL", "RISK_OFF", "CRISIS"}
    assert regime["regime_data_status"] in {"확정", "지연", "추정", "일부 결측"}


def test_risk_off_holds_tier2_candidate_as_crisis_hold():
    df = _strong_candidate(regime="RISK_OFF", total_score=160)
    df.loc[0, "vcp_status"] = "VCP_WARNING"
    out = ScoreFilterNode().run(
        [df],
        ScoreFilterParams(),
        ExecutionContext(as_of_date="2026-05-10", run_id="t"),
    )

    assert out.loc[0, "raw_score"] == 160
    assert out.loc[0, "gate_status"] == "HOLD"
    assert out.loc[0, "final_class"] == "CRISIS_HOLD"
    assert out.loc[0, "primary_bucket"] == "CRISIS_HOLD"
    assert "REGIME_CONFLICT" in out.loc[0, "risk_flags"]


def test_crisis_blocks_new_tier_issue():
    out = ScoreFilterNode().run(
        [_strong_candidate(regime="CRISIS", total_score=190)],
        ScoreFilterParams(),
        ExecutionContext(as_of_date="2026-05-10", run_id="t"),
    )

    assert out.loc[0, "gate_status"] == "BLOCK"
    assert pd.isna(out.loc[0, "effective_score"])
    assert out.loc[0, "final_class"] == "CRISIS_HOLD"
    assert "CRISIS_BLOCK" in out.loc[0, "risk_flags"]


def test_hard_gate_fail_blocks_high_raw_score():
    df = _strong_candidate(regime="RISK_ON", total_score=190)
    df.loc[0, "rs_rating"] = 30
    df.loc[0, "rs_status"] = "LOW_RS"
    df.loc[0, "ma_alignment_flag"] = "NOT_ALIGNED"
    df.loc[0, "liquidity_status"] = "ILLIQUID"

    out = ScoreFilterNode().run([df], ScoreFilterParams(), ExecutionContext(as_of_date="2026-05-10", run_id="t"))

    assert out.loc[0, "raw_score"] == 190
    assert out.loc[0, "gate_status"] == "FAIL"
    assert pd.isna(out.loc[0, "effective_score"])
    assert out.loc[0, "final_class"] == "REJECTED"
    assert out.loc[0, "hard_gate_reasons"]


def test_analysis_payload_exposes_market_regime_and_crisis_hold():
    scored = ScoreFilterNode().run(
        [_strong_candidate(regime="CRISIS", total_score=190)],
        ScoreFilterParams(),
        ExecutionContext(as_of_date="2026-05-10", run_id="t"),
    )
    logs = [
        SimpleNamespace(node_id="n1", node_type="universe", input_count=0, output_count=1, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[]),
        SimpleNamespace(node_id="n2", node_type="score_filter", input_count=1, output_count=1, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[]),
    ]
    payload = build_analysis_payload(SimpleNamespace(outputs={"n1": scored, "n2": scored}, node_logs=logs), {})

    assert payload["summary"]["crisis_hold_count"] == 1
    assert payload["summary"]["market_regime"]["CRISIS"] == 70
    assert payload["results"]["crisis_hold"][0]["final_class"] == "CRISIS_HOLD"
