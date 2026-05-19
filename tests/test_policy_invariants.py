"""
Policy invariant & schema preservation regression tests.

Covers user spec section 8:
  A. reason 보존 (TIER가 promotion_reasons를 잃지 않음)
  B. WATCHLIST reason fallback (watchlist_reasons → display_promotion_reasons)
  C. REJECTED reason (promotion_reasons=[] 유지)
  D. score_max test (총점이 95이면 95/210, 95/100이 아님)
  E. reason array formatting (numpy/pandas → list[str])
  F. Watch Alert invariant (REJECTED + watch_alert_flag=True 등 금지)
  G. VCP raw/effective separation
  H. Symbol presence 회귀 테스트 (모든 row가 동일 schema)
"""
import pytest
import pandas as pd
import numpy as np

from backend.alphaforge_policy import (
    normalize_result_schema,
    as_reason_list,
    first_non_empty_reason_list,
    classify_watch_alert,
    normalize_vcp_score,
    check_policy_invariants,
    validate_policy_invariants,
    build_promotion_reasons,
    build_display_fields,
    DEFAULT_SCORE_MAX,
)


# ---------------------------------------------------------------------------
# A. reason 보존 — TIER가 promotion_reasons를 절대 잃지 않음
# ---------------------------------------------------------------------------

class TestReasonPreservation:
    def test_tier2_promotion_reasons_preserved(self):
        row = {
            "primary_bucket": "TIER_2",
            "promotion_reasons": ["강한 주도주 후보", "RS 리더십 후보"],
            "symbol": "T2",
        }
        n = normalize_result_schema(row)
        assert n["tier_promotion_reasons"]
        assert n["promotion_reasons"]
        assert n["display_promotion_reasons"]
        assert "강한 주도주 후보" in n["display_promotion_reasons"]

    def test_tier3_promotion_reasons_preserved_via_alias(self):
        row = {
            "primary_bucket": "TIER_3",
            "tier_reasons": ["관찰 후보", "RS 리더십"],  # alias only
            "symbol": "T3",
        }
        n = normalize_result_schema(row)
        # alias가 있으면 promotion_reasons로 통일됨
        assert "관찰 후보" in n["promotion_reasons"]

    def test_normalize_does_not_overwrite_with_empty(self):
        """normalize_result_schema는 기존 non-empty reason을 빈 리스트로 덮어쓰지 않는다."""
        row = {
            "primary_bucket": "TIER_2",
            "promotion_reasons": ["A", "B"],
            "symbol": "X",
        }
        n = normalize_result_schema(row)
        assert n["promotion_reasons"] == ["A", "B"]
        # 두 번 normalize해도 보존
        n2 = normalize_result_schema(n)
        assert n2["promotion_reasons"] == ["A", "B"]


# ---------------------------------------------------------------------------
# B. WATCHLIST fallback (watchlist_reasons → display_promotion_reasons)
# ---------------------------------------------------------------------------

class TestWatchlistFallback:
    def test_watchlist_uses_watchlist_reasons_for_display(self):
        row = {
            "primary_bucket": "WATCHLIST",
            "watchlist_reasons": ["대형주 품질", "박스권 상단 근접"],
            "symbol": "W1",
        }
        n = normalize_result_schema(row)
        assert n["display_promotion_reasons"] == ["대형주 품질", "박스권 상단 근접"]

    def test_watchlist_falls_back_to_setup_reasons(self):
        row = {
            "primary_bucket": "WATCHLIST",
            "setup_reasons": ["베이스 형성"],
            "symbol": "W2",
        }
        n = normalize_result_schema(row)
        assert "베이스 형성" in n["display_promotion_reasons"]

    def test_watchlist_promotion_can_be_empty(self):
        """WATCHLIST는 promotion_reasons가 비어 있어도 watchlist_reasons로 화면 표시 가능."""
        row = {
            "primary_bucket": "WATCHLIST",
            "promotion_reasons": [],
            "watchlist_reasons": ["수급 강함"],
            "symbol": "W3",
        }
        n = normalize_result_schema(row)
        assert n["display_promotion_reasons"] == ["수급 강함"]


# ---------------------------------------------------------------------------
# C. REJECTED reason
# ---------------------------------------------------------------------------

class TestRejectedReasons:
    def test_rejected_promotion_is_empty(self):
        row = {
            "primary_bucket": "REJECTED",
            "rejected_reasons": ["RS 50 미만"],
            "symbol": "R1",
        }
        n = normalize_result_schema(row)
        assert n["promotion_reasons"] == []
        assert n["display_promotion_reasons"] == []
        assert n["rejected_reasons"] == ["RS 50 미만"]
        assert n["display_rejected_reasons"] == ["RS 50 미만"]


# ---------------------------------------------------------------------------
# D. score_max — run-level 210 보존
# ---------------------------------------------------------------------------

class TestScoreMax:
    def test_total_95_does_not_become_score_max_100(self):
        """가장 흔한 회귀 — total=95이면 95/100으로 표시되던 버그."""
        row = {"total_score": 95, "symbol": "X"}
        n = normalize_result_schema(row)
        assert n["score_max"] == 210.0, f"Expected 210, got {n['score_max']}"

    def test_total_125_score_max_210(self):
        row = {"total_score": 125, "symbol": "X"}
        assert normalize_result_schema(row)["score_max"] == 210.0

    def test_score_max_explicit_preserved(self):
        row = {"total_score": 125, "score_max": 250, "symbol": "X"}
        assert normalize_result_schema(row)["score_max"] == 250.0

    def test_score_pct_computed(self):
        row = {"total_score": 105, "symbol": "X"}
        n = normalize_result_schema(row)
        assert n["score_pct"] == 50.0

    def test_run_level_score_max_consistency(self):
        """동일 run의 모든 row가 같은 score_max를 가져야 한다."""
        rows = [
            {"total_score": 95, "symbol": f"S{i}", "primary_bucket": "WATCHLIST"}
            for i in range(5)
        ]
        normalized = [normalize_result_schema(r) for r in rows]
        score_maxes = {r["score_max"] for r in normalized}
        assert len(score_maxes) == 1, f"Inconsistent score_max: {score_maxes}"
        assert 210.0 in score_maxes


# ---------------------------------------------------------------------------
# E. reason array formatting (numpy/pandas → list[str])
# ---------------------------------------------------------------------------

class TestReasonArrayFormatting:
    def test_numpy_array_to_list(self):
        row = {
            "promotion_reasons": np.array(["A", "B", "C"]),
            "primary_bucket": "TIER_2",
            "symbol": "X",
        }
        n = normalize_result_schema(row)
        assert n["promotion_reasons"] == ["A", "B", "C"]
        assert isinstance(n["promotion_reasons"], list)

    def test_pandas_series_to_list(self):
        row = {
            "promotion_reasons": pd.Series(["X", "Y"]),
            "primary_bucket": "TIER_3",
            "symbol": "X",
        }
        n = normalize_result_schema(row)
        assert n["promotion_reasons"] == ["X", "Y"]

    def test_str_format_no_numpy_repr(self):
        row = {
            "promotion_reasons": np.array(["A", "B"]),
            "primary_bucket": "TIER_2",
            "symbol": "X",
        }
        n = normalize_result_schema(row)
        # promotion_reasons_str은 "A; B" 형태여야 함; "['A' 'B']" 형태 금지
        assert n["promotion_reasons_str"] == "A; B"
        assert "[" not in n["promotion_reasons_str"]


# ---------------------------------------------------------------------------
# F. Watch Alert invariants
# ---------------------------------------------------------------------------

class TestWatchAlertInvariants:
    def test_rejected_no_alert(self):
        row = {"primary_bucket": "REJECTED", "rs_percentile": 80}
        flag, t, action, reasons, excl, trace = classify_watch_alert(row)
        assert flag is False
        assert t == "NONE"

    def test_reverse_expansion_no_action(self):
        row = {
            "primary_bucket": "TIER_2", "rs_percentile": 85,
            "vcp_status": "REVERSE_EXPANSION",
            "breakout_status": "BREAKOUT_CONFIRMED",
            "ma_alignment_flag": "ALIGNED",
            "liquidity_status": "LIQUID",
        }
        flag, t, action, reasons, excl, trace = classify_watch_alert(row)
        assert action is False
        assert t != "ACTION_ALERT"

    def test_failed_breakout_no_action(self):
        row = {
            "primary_bucket": "WATCHLIST", "rs_percentile": 95,
            "vcp_status": "VCP_VALID",
            "breakout_status": "FAILED_BREAKOUT",
            "ma_alignment_flag": "ALIGNED",
            "liquidity_status": "LIQUID",
        }
        flag, t, action, reasons, excl, trace = classify_watch_alert(row)
        assert action is False

    def test_data_unit_warning_no_action(self):
        row = {
            "primary_bucket": "TIER_3", "rs_percentile": 88,
            "vcp_status": "VCP_VALID",
            "breakout_status": "NEAR_BREAKOUT",
            "data_unit_check": "DATA_UNIT_WARNING",
            "ma_alignment_flag": "ALIGNED",
            "liquidity_status": "LIQUID",
        }
        flag, t, action, reasons, excl, trace = classify_watch_alert(row)
        assert action is False
        assert t in {"DATA_REVIEW", "RISK_WATCH", "NONE"}

    def test_liquidity_uncertain_no_action(self):
        row = {
            "primary_bucket": "TIER_3", "rs_percentile": 90,
            "vcp_status": "VCP_VALID",
            "breakout_status": "NEAR_BREAKOUT",
            "ma_alignment_flag": "ALIGNED",
            "liquidity_status": "LIQUIDITY_UNCERTAIN",
        }
        flag, t, action, reasons, excl, trace = classify_watch_alert(row)
        assert action is False

    def test_action_alert_implies_action_flag(self):
        row = {
            "primary_bucket": "TIER_2", "rs_percentile": 85,
            "vcp_status": "VCP_VALID",
            "breakout_status": "BREAKOUT_CONFIRMED",
            "ma_alignment_flag": "ALIGNED",
            "liquidity_status": "LIQUID",
            "flow_total_score": 25,
        }
        flag, t, action, reasons, excl, trace = classify_watch_alert(row)
        assert flag is True
        assert t == "ACTION_ALERT"
        assert action is True

    def test_check_policy_invariants_rejected_with_alert(self):
        row = {
            "primary_bucket": "REJECTED",
            "watch_alert_flag": True,
            "watch_alert_type": "RISK_WATCH",
            "rs_percentile": 80,
        }
        errors = check_policy_invariants(row)
        assert any("REJECTED" in e for e in errors)


# ---------------------------------------------------------------------------
# G. VCP raw/effective separation
# ---------------------------------------------------------------------------

class TestVcpRawEffective:
    def test_low_rs_high_vcp_cross_weak(self):
        row = {
            "vcp_score": 78,
            "rs_percentile": 26.3,
            "ma_alignment_flag": "NOT_ALIGNED",
            "primary_bucket": "REJECTED",
        }
        raw, eff, disp, conf, cross = normalize_vcp_score(row)
        assert raw == 78.0
        assert eff <= 45.0
        assert disp <= 45.0
        assert conf == "CROSS_FACTOR_WEAK"
        assert cross is not None

    def test_high_vcp_aligned_strong_rs_no_penalty(self):
        row = {
            "vcp_score": 92,
            "rs_percentile": 90,
            "ma_alignment_flag": "ALIGNED",
            "primary_bucket": "TIER_1",
        }
        raw, eff, disp, conf, cross = normalize_vcp_score(row)
        assert raw == 92.0
        assert eff == 92.0  # 패널티 없음
        assert cross is None

    def test_rejected_high_vcp_invariant(self):
        """Rejected + raw VCP>=70인데 effective>45이면 invariant 위반."""
        row = {
            "primary_bucket": "REJECTED",
            "vcp_raw_score": 78,
            "vcp_effective_score": 78,  # 의도적으로 잘못된 상태
            "rs_percentile": 26.3,
        }
        errors = check_policy_invariants(row)
        assert any("effective" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# H. Schema regression tests
# ---------------------------------------------------------------------------

class TestSchemaRegression:
    def test_all_rows_same_schema(self):
        """다양한 입력에서도 normalize 후에는 동일한 핵심 필드를 가져야 한다."""
        required = {
            "symbol", "name", "primary_bucket", "total_score", "score_max",
            "rs_percentile", "vcp_status", "watch_alert_flag", "watch_alert_type",
            "promotion_reasons", "rejected_reasons", "display_promotion_reasons",
            "score_pct", "watchlist_reasons",
        }
        cases = [
            {"symbol": "S1", "primary_bucket": "TIER_1", "total_score": 165},
            {"symbol": "S2", "primary_bucket": "TIER_2", "total_score": 125},
            {"symbol": "S3", "primary_bucket": "WATCHLIST", "total_score": 95},
            {"symbol": "S4", "primary_bucket": "REJECTED", "total_score": 50},
            {"symbol": "S5"},  # 최소 입력
        ]
        for c in cases:
            n = normalize_result_schema(c)
            assert required.issubset(set(n.keys())), f"Missing fields in {c['symbol']}: {required - set(n.keys())}"

    def test_no_hardcoded_symbols_in_normalize(self):
        """normalize_result_schema는 종목명/코드별로 다르게 동작하지 않는다."""
        codes = ["005930", "298040", "000660", "999999"]
        for code in codes:
            row = {
                "symbol": code, "primary_bucket": "TIER_2",
                "promotion_reasons": ["X", "Y"], "total_score": 120,
            }
            n = normalize_result_schema(row)
            assert n["promotion_reasons"] == ["X", "Y"]
            assert n["score_max"] == 210.0


# ---------------------------------------------------------------------------
# Build promotion reasons (proper Tier/Watchlist split)
# ---------------------------------------------------------------------------

class TestBuildPromotionReasons:
    def test_tier2_has_meaningful_reasons(self):
        promo, watch = build_promotion_reasons(
            bucket="TIER_2",
            quality_factors=["RS 리더십", "수급 강함"],
            t1_restrictions=["돌파 IN_BOX"],
            rejected_reasons=[],
            row={
                "rs_percentile": 92, "vcp_status": "VCP_VALID",
                "breakout_status": "IN_BOX", "ma_alignment_flag": "ALIGNED",
                "liquidity_status": "LIQUID", "flow_total_score": 25,
            },
        )
        assert len(promo) >= 3, f"Tier 2 should have >= 3 promotion reasons, got: {promo}"
        assert "강한 주도주 후보" in promo
        assert any("RS" in p for p in promo)
        assert "확인 대기" in promo  # t1_restrictions 있음

    def test_watchlist_has_retention_reasons(self):
        promo, watch = build_promotion_reasons(
            bucket="WATCHLIST",
            quality_factors=["대형주 품질"],
            t1_restrictions=[],
            rejected_reasons=[],
            row={
                "rs_percentile": 65, "vcp_status": "BASE_BUILDING",
                "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID",
                "breakout_distance_pct": 8.0, "flow_total_score": 18,
            },
        )
        assert promo == []  # watchlist은 promotion_reasons 없음
        assert len(watch) >= 1
        assert any("대형주 품질" in w or "대형주" in w for w in watch)

    def test_rejected_empty_lists(self):
        promo, watch = build_promotion_reasons(
            bucket="REJECTED",
            quality_factors=[],
            t1_restrictions=[],
            rejected_reasons=["RS 50 미만"],
            row={"rs_percentile": 25},
        )
        assert promo == []
        assert watch == []


# ---------------------------------------------------------------------------
# validate_policy_invariants (batch)
# ---------------------------------------------------------------------------

class TestValidatePolicyInvariants:
    def test_clean_rows_no_violations(self):
        rows = [
            {
                "symbol": "S1", "name": "A", "primary_bucket": "TIER_2",
                "total_score": 125, "score_max": 210.0,
                "rs_percentile": 85, "vcp_status": "VCP_VALID",
                "liquidity_status": "LIQUID",
                "watch_alert_flag": True, "watch_alert_type": "ACTION_ALERT",
                "action_alert_flag": True,
                "watch_alert_reasons": ["TIER_2_LEADERSHIP"],
                "promotion_reasons": ["강한 주도주 후보"],
                "watchlist_reasons": [],
                "display_promotion_reasons": ["강한 주도주 후보"],
            },
        ]
        diag = validate_policy_invariants(rows)
        assert diag["policy_violation_count"] == 0
        assert diag["score_max_inconsistent_count"] == 0

    def test_score_max_inconsistent_detected(self):
        rows = [
            {"symbol": "S1", "score_max": 210, "primary_bucket": "TIER_2", "total_score": 100,
             "rs_percentile": 80, "vcp_status": "VCP_VALID", "liquidity_status": "LIQUID",
             "watch_alert_flag": False, "watch_alert_type": "NONE",
             "name": "X"},
            {"symbol": "S2", "score_max": 100, "primary_bucket": "WATCHLIST", "total_score": 50,
             "rs_percentile": 60, "vcp_status": "BASE_BUILDING", "liquidity_status": "LIQUID",
             "watch_alert_flag": False, "watch_alert_type": "NONE",
             "name": "Y"},
        ]
        diag = validate_policy_invariants(rows)
        assert diag["score_max_inconsistent_count"] >= 1

    def test_watch_alert_distribution(self):
        rows = [
            {"symbol": "A", "primary_bucket": "TIER_2", "watch_alert_type": "ACTION_ALERT", "watch_alert_flag": True, "action_alert_flag": True, "watch_alert_reasons": ["X"], "score_max": 210, "total_score": 120, "rs_percentile": 85, "vcp_status": "VCP_VALID", "liquidity_status": "LIQUID", "name": "A"},
            {"symbol": "B", "primary_bucket": "WATCHLIST", "watch_alert_type": "RISK_WATCH", "watch_alert_flag": True, "watch_alert_reasons": ["X"], "score_max": 210, "total_score": 80, "rs_percentile": 92, "vcp_status": "REVERSE_EXPANSION", "liquidity_status": "LIQUID", "name": "B"},
            {"symbol": "C", "primary_bucket": "REJECTED", "watch_alert_type": "NONE", "watch_alert_flag": False, "score_max": 210, "total_score": 30, "rs_percentile": 25, "vcp_status": "NOT_READY", "liquidity_status": "ILLIQUID", "name": "C"},
        ]
        diag = validate_policy_invariants(rows)
        dist = diag["watch_alert_type_distribution"]
        assert dist.get("ACTION_ALERT", 0) == 1
        assert dist.get("RISK_WATCH", 0) == 1
        assert dist.get("NONE", 0) == 1


# ---------------------------------------------------------------------------
# Display fields (UI helpers)
# ---------------------------------------------------------------------------

class TestBuildDisplayFields:
    def test_action_alert_display(self):
        row = {
            "primary_bucket": "TIER_2",
            "watch_alert_flag": True,
            "watch_alert_type": "ACTION_ALERT",
        }
        d = build_display_fields(row)
        assert d["alert_emoji"] == "○"
        assert d["alert_label"] == "NEAR BUY"
        assert d["bucket_display"] == "Tier 2 주도주 후보"

    def test_risk_watch_display(self):
        row = {
            "primary_bucket": "WATCHLIST",
            "watch_alert_flag": True,
            "watch_alert_type": "RISK_WATCH",
        }
        d = build_display_fields(row)
        assert d["alert_emoji"] == "⚠️"
        assert "RISK WATCH" == d["alert_label"]

    def test_data_review_display(self):
        d = build_display_fields({
            "primary_bucket": "TIER_3",
            "watch_alert_flag": True,
            "watch_alert_type": "DATA_REVIEW",
        })
        assert d["alert_emoji"] == "⚠️"
        assert d["alert_label"] == "RISK WATCH"

    def test_rejected_display(self):
        d = build_display_fields({"primary_bucket": "REJECTED", "watch_alert_flag": False})
        assert d["alert_display"] == ""
        assert d["bucket_display"] == "Rejected 제외"


# ---------------------------------------------------------------------------
# Hardcode-free invariant — verify production logic doesn't reference symbols
# ---------------------------------------------------------------------------

class TestNoHardcodedSymbols:
    def test_no_hyosung_in_score_filter(self):
        """nodes/score_filter.py에는 298040/효성중공업 하드코딩이 없어야 한다."""
        with open("/Users/miyoo1016/jason_octopus/nodes/score_filter.py") as f:
            content = f.read()
        assert "298040" not in content, "score_filter.py should not hardcode 298040"
        assert "효성중공업" not in content, "score_filter.py should not hardcode 효성중공업"

    def test_no_hyosung_in_universe(self):
        with open("/Users/miyoo1016/jason_octopus/nodes/universe.py") as f:
            content = f.read()
        assert "298040" not in content, "universe.py should not hardcode 298040"

    def test_no_hyosung_in_analysis_summary(self):
        with open("/Users/miyoo1016/jason_octopus/backend/analysis_summary.py") as f:
            content = f.read()
        # hyosung_trace 같은 진단 블록이 없어야 함
        assert "298040" not in content, "analysis_summary.py should not hardcode 298040"
        assert "hyosung_trace" not in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
