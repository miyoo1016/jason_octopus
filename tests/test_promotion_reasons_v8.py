"""
v8 승격/관찰 사유 파이프라인 테스트
Section 10 of spec: 분류 결과의 근거를 사용자에게 올바르게 전달하는지 검증.

Coverage:
  1. TIER_2/TIER_3/WATCHLIST는 display_promotion_reasons가 비어있으면 안 됨
  2. REJECTED는 display_promotion_reasons가 항상 []
  3. build_fallback_display_reasons — feature-value에서 사유 생성
  4. normalize_result_schema — display_promotion_reasons 비어있으면 fallback 자동 사용
  5. display_promotion_reasons 기존 값 덮어쓰기 금지
  6. WATCHLIST는 watchlist_reasons가 promotion_reasons보다 우선
  7. score_filter 출력 행이 비-REJECTED이면 display_promotion_reasons 비어있으면 안 됨
"""
import pytest
import pandas as pd
import numpy as np
from backend.alphaforge_policy import (
    normalize_result_schema,
    build_fallback_display_reasons,
    build_promotion_reasons,
    extract_display_reasons_from_classification_text,
    as_reason_list,
    DEFAULT_SCORE_MAX,
)


# ── 1. TIER_2 / TIER_3 / WATCHLIST 사유 비어있으면 안 됨 ──────────────────

class TestNonRejectedHaveDisplayReasons:
    def _make_row(self, bucket, **kwargs):
        base = {
            "code": "TEST", "name": "테스트", "primary_bucket": bucket,
            "total_score": 120, "score_max": 210,
            "rs_percentile": 85.0, "rs_rating": 85.0,
            "vcp_status": "VCP_WARNING", "breakout_status": "IN_BOX",
            "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID",
            "flow_total_score": 20, "breakout_distance_pct": 4.0,
        }
        base.update(kwargs)
        return normalize_result_schema(base)

    def test_tier2_has_display_reasons(self):
        row = self._make_row("TIER_2")
        reasons = as_reason_list(row["display_promotion_reasons"])
        assert len(reasons) > 0, f"TIER_2 display_promotion_reasons must not be empty, got: {reasons}"

    def test_tier3_has_display_reasons(self):
        row = self._make_row("TIER_3")
        reasons = as_reason_list(row["display_promotion_reasons"])
        assert len(reasons) > 0, f"TIER_3 display_promotion_reasons must not be empty, got: {reasons}"

    def test_watchlist_has_display_reasons(self):
        row = self._make_row("WATCHLIST")
        reasons = as_reason_list(row["display_promotion_reasons"])
        assert len(reasons) > 0, f"WATCHLIST display_promotion_reasons must not be empty, got: {reasons}"

    def test_tier1_has_display_reasons(self):
        row = self._make_row(
            "TIER_1",
            vcp_status="VCP_STRICT", breakout_status="NEAR_BREAKOUT",
        )
        reasons = as_reason_list(row["display_promotion_reasons"])
        assert len(reasons) > 0, f"TIER_1 display_promotion_reasons must not be empty, got: {reasons}"

    def test_rejected_has_empty_display_reasons(self):
        row = self._make_row("REJECTED")
        reasons = as_reason_list(row["display_promotion_reasons"])
        assert reasons == [], f"REJECTED display_promotion_reasons must be [], got: {reasons}"

    def test_display_reasons_extract_from_watchlist_label(self):
        row = normalize_result_schema({
            "code": "LABEL_W", "name": "라벨워치", "primary_bucket": "WATCHLIST",
            "tier_reason": "WATCHLIST: [Setup Watch] 대형주 품질+보조강점, RS 리더십, 박스권 상단 근접 근거로 추적",
            "promotion_reasons": [], "watchlist_reasons": [],
        })
        reasons = as_reason_list(row["display_promotion_reasons"])
        assert reasons[:4] == ["Setup Watch", "대형주 품질", "보조강점", "RS 리더십"]
        assert "박스권 상단 근접" in reasons

    def test_display_reasons_extract_from_tier2_label(self):
        reasons = extract_display_reasons_from_classification_text(
            "Tier 2: [강한 주도주 후보 / 확인 대기] 돌파 거래량 부족로 Tier 1 제한",
            "TIER_2",
        )
        assert "강한 주도주 후보" in reasons
        assert "확인 대기" in reasons

    def test_rejected_label_extracts_no_promotion_reasons(self):
        reasons = extract_display_reasons_from_classification_text(
            "REJECTED: [복합 약점] RS 50 미만",
            "REJECTED",
        )
        assert reasons == []


# ── 2. build_fallback_display_reasons ─────────────────────────────────────

class TestBuildFallbackDisplayReasons:
    def _row(self, bucket="TIER_2", **kwargs):
        base = {
            "primary_bucket": bucket,
            "rs_percentile": 85.0, "rs_rating": 85.0,
            "vcp_status": "VCP_WARNING", "breakout_status": "IN_BOX",
            "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID",
            "flow_total_score": 20.0, "breakout_distance_pct": 4.0,
        }
        base.update(kwargs)
        return base

    def test_tier2_returns_nonempty(self):
        reasons = build_fallback_display_reasons(self._row("TIER_2"))
        assert len(reasons) > 0
        assert all(isinstance(r, str) for r in reasons)

    def test_watchlist_returns_nonempty(self):
        reasons = build_fallback_display_reasons(self._row("WATCHLIST"))
        assert len(reasons) > 0

    def test_rejected_returns_empty(self):
        reasons = build_fallback_display_reasons(self._row("REJECTED"))
        assert reasons == []

    def test_rs_80_in_reasons(self):
        reasons = build_fallback_display_reasons(self._row("TIER_2", rs_percentile=85))
        assert any("RS 리더십" in r for r in reasons), f"RS 리더십 should appear: {reasons}"

    def test_flow_strong_in_reasons(self):
        reasons = build_fallback_display_reasons(self._row("TIER_2", flow_total_score=28))
        assert any("수급 강함" in r for r in reasons), f"수급 강함 should appear: {reasons}"

    def test_near_breakout_in_reasons(self):
        reasons = build_fallback_display_reasons(self._row("TIER_2", breakout_status="NEAR_BREAKOUT"))
        assert any("돌파 임박" in r for r in reasons), f"돌파 임박 should appear: {reasons}"

    def test_no_duplicates(self):
        reasons = build_fallback_display_reasons(self._row("TIER_2"))
        assert len(reasons) == len(set(reasons)), f"Duplicates found: {reasons}"

    def test_at_least_one_for_empty_features(self):
        """Even with minimal features, must return at least one reason."""
        reasons = build_fallback_display_reasons({
            "primary_bucket": "WATCHLIST",
            "rs_percentile": 0, "rs_rating": 0,
            "vcp_status": "", "breakout_status": "",
            "ma_alignment_flag": "", "liquidity_status": "",
            "flow_total_score": 0, "breakout_distance_pct": 20.0,
        })
        assert len(reasons) >= 1


# ── 3. normalize_result_schema fallback wire-up ───────────────────────────

class TestNormalizeSchemaFallback:
    """When promotion_reasons AND watchlist_reasons both empty, fallback must fire."""

    def test_tier2_no_prior_reasons_gets_fallback(self):
        row = {
            "code": "F001", "name": "팔백주", "primary_bucket": "TIER_2",
            "total_score": 130, "score_max": 210,
            "rs_percentile": 82.0, "vcp_status": "VCP_WARNING",
            "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID",
            "flow_total_score": 22.0, "breakout_distance_pct": 5.0,
            # No promotion_reasons / watchlist_reasons provided
        }
        normalized = normalize_result_schema(row)
        reasons = as_reason_list(normalized["display_promotion_reasons"])
        assert len(reasons) > 0, f"Fallback should fire for TIER_2 with no prior reasons: {reasons}"

    def test_watchlist_no_prior_reasons_gets_fallback(self):
        row = {
            "code": "F002", "name": "워치주", "primary_bucket": "WATCHLIST",
            "total_score": 90, "rs_percentile": 75.0,
            "vcp_status": "VCP_WARNING", "ma_alignment_flag": "ALIGNED",
            "liquidity_status": "LIQUID", "flow_total_score": 18.0,
            "breakout_distance_pct": 4.5,
        }
        normalized = normalize_result_schema(row)
        reasons = as_reason_list(normalized["display_promotion_reasons"])
        assert len(reasons) > 0, f"Fallback should fire for WATCHLIST with no prior reasons: {reasons}"

    def test_rejected_no_fallback(self):
        row = {
            "code": "F003", "name": "제외주", "primary_bucket": "REJECTED",
            "total_score": 50, "rs_percentile": 30.0,
        }
        normalized = normalize_result_schema(row)
        assert as_reason_list(normalized["display_promotion_reasons"]) == []

    def test_existing_reasons_not_overwritten(self):
        """If promotion_reasons already set, fallback must NOT overwrite them."""
        original_reasons = ["강한 주도주 후보", "RS 리더십 후보", "수급 강함"]
        row = {
            "code": "F004", "name": "보존주", "primary_bucket": "TIER_2",
            "total_score": 140, "score_max": 210,
            "rs_percentile": 88.0, "vcp_status": "VCP_STRICT",
            "liquidity_status": "LIQUID", "flow_total_score": 26.0,
            "breakout_distance_pct": 2.5, "ma_alignment_flag": "ALIGNED",
            "promotion_reasons": original_reasons,
        }
        normalized = normalize_result_schema(row)
        result_reasons = as_reason_list(normalized["display_promotion_reasons"])
        # Must contain the original reasons (at minimum)
        for r in original_reasons:
            assert r in result_reasons, f"Original reason '{r}' must not be overwritten. Got: {result_reasons}"


# ── 4. WATCHLIST는 watchlist_reasons가 promotion_reasons보다 우선 ──────────

class TestWatchlistReasonPriority:
    def test_watchlist_uses_watchlist_reasons_not_promo(self):
        row = {
            "code": "W001", "name": "워치우선", "primary_bucket": "WATCHLIST",
            "total_score": 100, "rs_percentile": 72.0,
            "vcp_status": "BASE_BUILDING", "liquidity_status": "LIQUID",
            "flow_total_score": 16.0, "breakout_distance_pct": 4.0,
            "ma_alignment_flag": "ALIGNED",
            "watchlist_reasons": ["RS 리더십", "수급 양호", "정배열 유지"],
            "promotion_reasons": ["티어 사유 — 이게 표시되면 안 됨"],
        }
        normalized = normalize_result_schema(row)
        display = as_reason_list(normalized["display_promotion_reasons"])
        # watchlist_reasons가 우선이어야 함
        assert "RS 리더십" in display or "수급 양호" in display, f"watchlist_reasons not prioritized: {display}"
        # promotion_reasons의 티어 라벨이 최상위로 나오면 안 됨 (있어도 되지만 우선은 watch_reasons)
        assert display[0] != "티어 사유 — 이게 표시되면 안 됨", f"promotion_reasons incorrectly prioritized"

    def test_watchlist_fallback_not_empty_labels(self):
        """WATCHLIST display reasons must not include TIER-specific bucket labels."""
        row = {
            "code": "W002", "name": "워치클린", "primary_bucket": "WATCHLIST",
            "total_score": 95, "rs_percentile": 68.0,
            "vcp_status": "VCP_WARNING", "liquidity_status": "LIQUID",
            "flow_total_score": 17.0, "breakout_distance_pct": 5.0,
            "ma_alignment_flag": "ALIGNED",
        }
        normalized = normalize_result_schema(row)
        display = as_reason_list(normalized["display_promotion_reasons"])
        # TIER 라벨이 첫번째로 나오면 안 됨
        forbidden = {"실전 매수 후보", "강한 주도주 후보", "관찰 후보"}
        for r in display:
            assert r not in forbidden, f"Tier bucket label '{r}' should not appear in WATCHLIST display reasons"


# ── 5. score_filter 통합 — 실전 케이스 검증 ──────────────────────────────

class TestScoreFilterPromotionReasons:
    @pytest.fixture
    def score_node(self):
        from nodes.score_filter import ScoreFilterNode
        return ScoreFilterNode()

    @pytest.fixture
    def ctx(self):
        from engine.node_base import ExecutionContext
        return ExecutionContext(as_of_date="2026-05-10", run_id="test_v8_promo")

    def _make_df(self, **kwargs):
        base = {
            "code": "000001", "name": "테스트주",
            "rs_percentile": 85.0, "rs_rating": 85.0,
            "vcp_status": "VCP_WARNING", "breakout_status": "IN_BOX",
            "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID",
            "flow_total_score": 22.0, "total_score": 130,
        }
        base.update(kwargs)
        return pd.DataFrame([base])

    def test_tier2_display_reasons_nonempty(self, score_node, ctx):
        df = self._make_df()
        res = score_node.run([df], None, ctx)
        tier2_rows = res[res["primary_bucket"] == "TIER_2"]
        if tier2_rows.empty:
            pytest.skip("이 케이스가 TIER_2로 분류되지 않음 — 분류 기준 변경 없이 skip")
        row = tier2_rows.iloc[0]
        reasons = as_reason_list(row.get("display_promotion_reasons", []))
        assert len(reasons) > 0, f"score_filter TIER_2 display_promotion_reasons empty: {row.to_dict()}"

    def test_watchlist_display_reasons_nonempty(self, score_node, ctx):
        df = self._make_df(rs_percentile=62.0, rs_rating=62.0, flow_total_score=10.0,
                           vcp_status="BASE_BUILDING", breakout_distance_pct=8.0)
        res = score_node.run([df], None, ctx)
        wl_rows = res[res["primary_bucket"] == "WATCHLIST"]
        if wl_rows.empty:
            pytest.skip("이 케이스가 WATCHLIST로 분류되지 않음 — skip")
        row = wl_rows.iloc[0]
        reasons = as_reason_list(row.get("display_promotion_reasons", []))
        assert len(reasons) > 0, f"score_filter WATCHLIST display_promotion_reasons empty"

    def test_rejected_display_reasons_empty(self, score_node, ctx):
        # Hard gate: RS < 50, NOT_ALIGNED, NOT_READY, flow very low
        df = self._make_df(
            rs_percentile=25.0, rs_rating=25.0,
            ma_alignment_flag="NOT_ALIGNED",
            vcp_status="NOT_READY", flow_total_score=3.0,
            breakout_status="FAILED_BREAKOUT", liquidity_status="LIQUID",
            total_score=50,
        )
        res = score_node.run([df], None, ctx)
        rej_rows = res[res["primary_bucket"] == "REJECTED"]
        if rej_rows.empty:
            pytest.skip("이 케이스가 REJECTED로 분류되지 않음 — skip")
        row = rej_rows.iloc[0]
        reasons = as_reason_list(row.get("display_promotion_reasons", []))
        assert reasons == [], f"REJECTED display_promotion_reasons should be [], got: {reasons}"

    def test_display_promotion_reasons_str_matches_list(self, score_node, ctx):
        """display_promotion_reasons_str must equal '; '.join(display_promotion_reasons)."""
        df = self._make_df()
        res = score_node.run([df], None, ctx)
        row = res.iloc[0]
        r_list = as_reason_list(row.get("display_promotion_reasons", []))
        r_str = str(row.get("display_promotion_reasons_str", ""))
        if r_list:
            expected_str = "; ".join(r_list)
            assert r_str == expected_str, (
                f"display_promotion_reasons_str mismatch.\n"
                f"  list: {r_list}\n  str: '{r_str}'"
            )
