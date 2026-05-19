import sys
import os
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.alphaforge_policy import infer_recommendation, normalize_result_schema
from backend.alphaforge_export import format_recommendation_console

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
        "total_score": 100,
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

def test_recommendation_formatter():
    # 삼성전기형 케이스
    samsung = _make_row(
        code="009150",
        name="삼성전기",
        final_label="PRIORITY_WATCH",
        rs_percentile=92.7,
        vcp_display_score=30,
        vcp_status="NO_VCP",
        ma_alignment_flag="ALIGNED",
        flow_total_score=20,
        failed_buy_gates=["VCP_SCORE_BELOW_60", "BREAKOUT_STATUS_NOT_READY"]
    )
    # REVERSE_EXPANSION 케이스
    hynix = _make_row(
        code="000660",
        name="SK하이닉스",
        final_label="RISK_WATCH",
        rs_percentile=95,
        vcp_status="REVERSE_EXPANSION"
    )
    df = pd.DataFrame([samsung, hynix])
    text = format_recommendation_console(df)

    # 삼성전기 출력 확인
    assert "STARTER_POSITION" in text
    assert "15%" in text
    assert "진입 트리거:" in text
    assert "무효화 조건:" in text

    # SK하이닉스 출력 확인
    assert "WATCH_ONLY" in text
    assert "0%" in text
    assert "추격 매수 금지" in text

    print("✅ test_recommendation_formatter passed")

def test_analysis_summary_buy_now_warning():
    from backend.analysis_summary import build_analysis_payload
    from types import SimpleNamespace

    # BUY_NOW 없음
    samsung = _make_row(
        code="009150",
        name="삼성전기",
        final_label="PRIORITY_WATCH",
        rs_percentile=92.7,
        vcp_display_score=30,
        vcp_status="NO_VCP",
        ma_alignment_flag="ALIGNED",
        flow_total_score=20,
        failed_buy_gates=["VCP_SCORE_BELOW_60", "BREAKOUT_STATUS_NOT_READY"]
    )
    # mock result class
    class MockResult:
        success = True
        error = None
        run_id = "test_run"
        as_of_date = "2026-05-19"
        outputs = {"score_filter": pd.DataFrame([samsung])}
        node_logs = [SimpleNamespace(node_id="score_filter", node_type="score_filter", input_count=1, output_count=1, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[])]

    payload = build_analysis_payload(MockResult(), {}, "2026-05-19")
    summary = payload["summary"]
    assert "top_recommendations" in summary
    assert len(summary["top_recommendations"]) == 1
    assert summary["top_recommendations"][0]["name"] == "삼성전기"
    assert summary["top_recommendations_message"] == "BUY_NOW 없음. 조건부/소액탐색 후보만 존재"
    print("✅ test_analysis_summary_buy_now_warning passed")


def test_js_report_generator_output():
    import os
    import re
    js_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "strategy-builder-actions.js"))
    with open(js_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 실제 JS 템플릿에 해당 필드들이 렌더링되도록 삽입되었는지 문자열 검색으로 회귀 확인
    assert "추천: ${recAction} | 순위 ${recRank} | 점수 ${recScore} | 권장비중 ${recSize}%" in content, "추천 필드 누락"
    assert "추천 사유: ${recReason}" in content, "추천 사유 누락"
    assert "진입 트리거: ${recTrigger}" in content, "진입 트리거 누락"
    assert "무효화 조건: ${recInvalidation}" in content, "무효화 조건 누락"
    assert "BUY_NOW 없음. 조건부/소액탐색 후보만 존재" in content, "BUY_NOW 부재 경고 누락"
    assert "[오늘의 추천 TOP 3]" in content, "Top 3 헤더 누락"
    print("✅ test_js_report_generator_output passed")

if __name__ == "__main__":
    test_buy_candidate_is_buy_now()
    test_samsung_electro_mechanics_starter_position()
    test_reverse_expansion_watch_only()
    test_conditional_buy()
    test_data_quality_fatal()
    test_failed_buy_gates_over_3()
    test_rejected_complex_weakness()
    test_rank_logic_in_analysis_summary()
    test_recommendation_formatter()
    test_analysis_summary_buy_now_warning()
    test_js_report_generator_output()
    print("\n🎉 All tests passed!")
