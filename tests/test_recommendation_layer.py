import sys
import os
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.alphaforge_policy import infer_recommendation, normalize_result_schema

def _make_row(**kwargs):
    defaults = {
        "final_label": "SETUP_WATCH",
        "buy_gate_passed": False,
        "failed_buy_gates": [],
        "vcp_status": "VCP_FORMING",
        "rs_percentile": 70,
        "vcp_display_score": 60,
        "ma_alignment_flag": "ALIGNED",
        "flow_total_score": 15,
        "breakout_status": "IN_BOX",
        "primary_bucket": "TIER_3",
    }
    defaults.update(kwargs)
    return defaults

def test_buy_candidate_is_buy_now():
    row = _make_row(final_label="BUY_CANDIDATE", buy_gate_passed=True)
    rec = infer_recommendation(row)
    assert rec["recommendation_action"] == "BUY_NOW"
    assert rec["suggested_position_size"] in [30, 40, 50]
    print("✅ test_buy_candidate_is_buy_now passed")

def test_samsung_electro_mechanics_starter_position():
    # RS 92.7, VCP 30, NO_VCP, MA정배열, 수급 양호
    row = _make_row(
        final_label="PRIORITY_WATCH",
        rs_percentile=92.7,
        vcp_display_score=30,
        vcp_status="NO_VCP",
        ma_alignment_flag="ALIGNED",
        flow_total_score=20,
        failed_buy_gates=["VCP_SCORE_BELOW_60", "BREAKOUT_STATUS_NOT_READY"]
    )
    rec = infer_recommendation(row)
    assert rec["recommendation_action"] == "STARTER_POSITION"
    assert "소액 탐색" in rec["recommendation_reason"]
    assert rec["suggested_position_size"] in [10, 15]
    print("✅ test_samsung_electro_mechanics_starter_position passed")

def test_reverse_expansion_watch_only():
    row = _make_row(
        final_label="RISK_WATCH",
        rs_percentile=95,
        vcp_status="REVERSE_EXPANSION"
    )
    rec = infer_recommendation(row)
    assert rec["recommendation_action"] == "WATCH_ONLY"
    assert rec["suggested_position_size"] == 0
    print("✅ test_reverse_expansion_watch_only passed")

def test_conditional_buy():
    # VCP 45~59 + RS80 + MA정배열
    row = _make_row(
        rs_percentile=85,
        vcp_display_score=50,
        ma_alignment_flag="ALIGNED",
        failed_buy_gates=["VCP_SCORE_BELOW_60"]
    )
    rec = infer_recommendation(row)
    assert rec["recommendation_action"] == "CONDITIONAL_BUY"
    assert rec["suggested_position_size"] in [20, 25, 30]
    print("✅ test_conditional_buy passed")

def test_data_quality_fatal():
    row = _make_row(
        failed_buy_gates=["DATA_QUALITY_FATAL"],
        rs_percentile=90,
        vcp_display_score=80
    )
    rec = infer_recommendation(row)
    assert rec["recommendation_action"] == "AVOID"
    assert rec["suggested_position_size"] == 0
    print("✅ test_data_quality_fatal passed")

def test_failed_buy_gates_over_3():
    row = _make_row(
        rs_percentile=95,
        vcp_display_score=40,
        failed_buy_gates=["A", "B", "C", "D"]
    )
    rec = infer_recommendation(row)
    assert rec["recommendation_action"] not in ["BUY_NOW", "CONDITIONAL_BUY", "STARTER_POSITION"]
    print("✅ test_failed_buy_gates_over_3 passed")

def test_rejected_complex_weakness():
    row = _make_row(
        primary_bucket="REJECTED",
        final_label="REJECTED",
        rejected_reasons=["A", "B"]
    )
    rec = infer_recommendation(row)
    assert rec["recommendation_action"] == "AVOID"
    print("✅ test_rejected_complex_weakness passed")

def test_rank_logic_in_analysis_summary():
    # We will simulate the rank logic from analysis_summary by using a mock dataframe
    data = [
        _make_row(code="A", recommendation_action="WATCH_ONLY", recommendation_score=50),
        _make_row(code="B", recommendation_action="CONDITIONAL_BUY", recommendation_score=70),
        _make_row(code="C", recommendation_action="BUY_NOW", recommendation_score=80),
        _make_row(code="D", recommendation_action="STARTER_POSITION", recommendation_score=60),
        _make_row(code="E", recommendation_action="BUY_NOW", recommendation_score=90),
        _make_row(code="F", recommendation_action="AVOID", recommendation_score=10),
    ]
    df = pd.DataFrame(data)
    
    action_priority = {"BUY_NOW": 5, "CONDITIONAL_BUY": 4, "STARTER_POSITION": 3, "WATCH_ONLY": 2, "AVOID": 1}
    df["_action_priority"] = df["recommendation_action"].map(action_priority).fillna(0)
    df = df.sort_values(by=["_action_priority", "recommendation_score"], ascending=[False, False])
    
    df["recommendation_rank"] = None
    top_mask = df["recommendation_action"].isin({"BUY_NOW", "CONDITIONAL_BUY", "STARTER_POSITION"})
    top_indices = df[top_mask].index[:3]
    for idx, i in enumerate(top_indices):
        df.at[i, "recommendation_rank"] = idx + 1
    
    ranked = df[df["recommendation_rank"].notna()].sort_values("recommendation_rank")
    assert len(ranked) == 3
    assert ranked.iloc[0]["code"] == "E" # BUY_NOW, score 90
    assert ranked.iloc[1]["code"] == "C" # BUY_NOW, score 80
    assert ranked.iloc[2]["code"] == "B" # CONDITIONAL_BUY, score 70
    print("✅ test_rank_logic_in_analysis_summary passed")

if __name__ == "__main__":
    test_buy_candidate_is_buy_now()
    test_samsung_electro_mechanics_starter_position()
    test_reverse_expansion_watch_only()
    test_conditional_buy()
    test_data_quality_fatal()
    test_failed_buy_gates_over_3()
    test_rejected_complex_weakness()
    test_rank_logic_in_analysis_summary()
    print("\n🎉 All tests passed!")
