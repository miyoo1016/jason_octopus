"""
tests/test_operation_report.py
Phase 5 운영 리포트 / 판정 품질 대시보드 단위 테스트.
투자 로직(VCP/RS/BUY 게이트) 변경 없이 build_operation_report만 검증한다.
"""
import sys
import os
import pandas as pd

# 프로젝트 루트가 PYTHONPATH에 있어야 함
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.analysis_summary import build_operation_report


def _make_report(**kwargs) -> dict:
    """Convenience: build a minimal operation_report from args."""
    final_df = kwargs.pop("final_df", pd.DataFrame())
    summary = kwargs.pop("summary", {})
    diagnostics = kwargs.pop("diagnostics", {})
    perf_summary = kwargs.pop("perf_summary", {"status": "DATA_INSUFFICIENT"})
    market_regime = kwargs.pop("market_regime", {"dominant_regime": "NEUTRAL"})
    return build_operation_report(
        final_df=final_df,
        summary=summary,
        diagnostics=diagnostics,
        perf_summary=perf_summary,
        market_regime=market_regime,
    )


# ── 1. BUY_CANDIDATE 0개일 때 메시지 정상 ───────────────────────────────────
def test_no_buy_candidate_message():
    df = pd.DataFrame([
        {"code": "A001", "close": 10000, "final_label": "PRIORITY_WATCH", "failed_buy_gates": ["RS_BELOW_80"]},
    ])
    summary = {"buy_candidate_count": 0, "near_buy_count": 0,
               "priority_watch_count": 1, "risk_watch_count": 0, "rejected_count": 0}
    perf = {"status": "DATA_INSUFFICIENT"}
    report = _make_report(final_df=df, summary=summary, perf_summary=perf)

    assert report["buy_candidate_count"] == 0
    assert "없음" in report["operator_message"] or "부족" in report["operator_message"]
    print("✅ test_no_buy_candidate_message passed:", report["operator_message"])


# ── 2. BUY_CANDIDATE 존재 시 메시지 정상 ────────────────────────────────────
def test_buy_candidate_message():
    df = pd.DataFrame([
        {"code": "A001", "close": 70000, "final_label": "BUY_CANDIDATE",
         "buy_gate_passed": True, "failed_buy_gates": []},
    ])
    summary = {"buy_candidate_count": 1, "near_buy_count": 0,
               "priority_watch_count": 0, "risk_watch_count": 0, "rejected_count": 0}
    report = _make_report(final_df=df, summary=summary)

    assert report["buy_candidate_count"] == 1
    assert "하드 게이트" in report["operator_message"]
    assert report["status"] == "READY"
    print("✅ test_buy_candidate_message passed:", report["operator_message"])


# ── 3. failed_buy_gates Top 5 집계 ─────────────────────────────────────────
def test_top_blocking_reasons():
    rows = []
    # VCP_SCORE_BELOW_60 × 5, RS_BELOW_80 × 3, BREAKOUT_NOT_READY × 1
    for _ in range(5):
        rows.append({"code": f"X{_}", "close": 1000, "final_label": "REJECTED",
                     "failed_buy_gates": ["VCP_SCORE_BELOW_60", "RS_BELOW_80"]})
    rows.append({"code": "Y1", "close": 2000, "final_label": "REJECTED",
                 "failed_buy_gates": ["BREAKOUT_NOT_READY"]})
    df = pd.DataFrame(rows)
    summary = {"buy_candidate_count": 0, "near_buy_count": 0,
               "priority_watch_count": 0, "risk_watch_count": 0, "rejected_count": 6}
    report = _make_report(final_df=df, summary=summary)

    reasons = {r["reason"]: r["count"] for r in report["top_blocking_reasons"]}
    assert reasons.get("VCP_SCORE_BELOW_60", 0) == 5
    assert reasons.get("RS_BELOW_80", 0) == 5
    assert reasons.get("BREAKOUT_NOT_READY", 0) == 1
    assert len(report["top_blocking_reasons"]) <= 5
    print("✅ test_top_blocking_reasons passed:", report["top_blocking_reasons"])


# ── 4. quality_score가 0~100 범위 ───────────────────────────────────────────
def test_quality_score_range():
    # Minimal empty case → should be low but clamped ≥ 0
    report_empty = _make_report(final_df=pd.DataFrame(), summary={})
    assert 0 <= report_empty["quality_score"] <= 100

    # Rich case with labels and gates → should be high
    df = pd.DataFrame([
        {"code": "A001", "close": 70000, "final_label": "BUY_CANDIDATE",
         "buy_gate_passed": True, "failed_buy_gates": [],
         "vcp_component_scores": {"contraction": 80}},
    ])
    summary = {"buy_candidate_count": 1, "near_buy_count": 0,
               "priority_watch_count": 0, "risk_watch_count": 0, "rejected_count": 0}
    perf = {"status": "READY"}
    report_rich = _make_report(final_df=df, summary=summary, perf_summary=perf)
    assert 0 <= report_rich["quality_score"] <= 100
    assert report_rich["quality_score"] > report_empty["quality_score"]
    print("✅ test_quality_score_range passed — empty:", report_empty["quality_score"],
          "rich:", report_rich["quality_score"])


# ── 5. 데이터 부족 시 DATA_INSUFFICIENT 처리 ──────────────────────────────
def test_data_insufficient_handling():
    report = _make_report(final_df=pd.DataFrame(), summary={},
                          perf_summary={"status": "DATA_INSUFFICIENT"})
    assert report["status"] == "DATA_INSUFFICIENT"
    assert report["quality_score"] <= 50  # penalised
    print("✅ test_data_insufficient_handling passed:", report["status"],
          "quality_score:", report["quality_score"])


# ── 6. 기존 performance_summary와 충돌 없음 ────────────────────────────────
def test_no_conflict_with_performance_summary():
    """operation_report is built separately; performance_summary fields are untouched."""
    df = pd.DataFrame([
        {"code": "A001", "close": 70000, "final_label": "NEAR_BUY",
         "failed_buy_gates": ["VCP_SCORE_BELOW_60"]},
    ])
    summary = {"buy_candidate_count": 0, "near_buy_count": 1,
               "priority_watch_count": 0, "risk_watch_count": 0, "rejected_count": 0}
    perf_summary = {
        "status": "DATA_INSUFFICIENT",
        "by_label": {},
        "message": "성과 추적 데이터 부족",
    }
    report = _make_report(final_df=df, summary=summary, perf_summary=perf_summary)

    # operation_report should not mutate perf_summary
    assert perf_summary["status"] == "DATA_INSUFFICIENT"
    assert "by_label" in perf_summary
    assert "quality_score" in report          # op report has own field
    assert "quality_score" not in perf_summary  # perf_summary unchanged
    print("✅ test_no_conflict_with_performance_summary passed")


if __name__ == "__main__":
    test_no_buy_candidate_message()
    test_buy_candidate_message()
    test_top_blocking_reasons()
    test_quality_score_range()
    test_data_insufficient_handling()
    test_no_conflict_with_performance_summary()
    print("\n🎉 All operation_report tests passed!")
