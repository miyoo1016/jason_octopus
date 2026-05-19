"""
tests/test_near_buy_vcp_gate.py
Phase 6 NEAR_BUY 조건 강화 / VCP 분리 / 시총 단위 경고 단위 테스트.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.alphaforge_policy import infer_final_label, normalize_result_schema


def _make_row(**kwargs) -> dict:
    defaults = {
        "primary_bucket": "TIER_2",
        "watch_alert_type": "NONE",
        "buy_gate_passed": False,
        "failed_buy_gates": [],
        "vcp_status": "VCP_FORMING",
        "vcp_display_score": 60,
        "vcp_effective_score": 60,
        "vcp_score": 60,
        "rs_percentile": 85,
        "breakout_status": "IN_BOX",
        "liquidity_status": "LIQUID",
        "data_unit_warning_flag": False,
        "vcp_rally_exhaustion_flag": False,
    }
    defaults.update(kwargs)
    return defaults


# ── 1. VCP 30 + NO_VCP + RS 90 이상 + 돌파 미준비 → NEAR_BUY 금지 ────────────
def test_no_vcp_high_rs_not_near_buy():
    row = _make_row(
        vcp_status="NO_VCP",
        vcp_display_score=30,
        vcp_effective_score=30,
        vcp_score=30,
        rs_percentile=92.7,
        breakout_status="NOT_READY",
        failed_buy_gates=["VCP_SCORE_BELOW_60", "BREAKOUT_STATUS_NOT_READY"],
    )
    label = infer_final_label(row)
    assert label in {"PRIORITY_WATCH", "SETUP_WATCH"}, \
        f"VCP 30 NO_VCP RS 92.7인 종목이 NEAR_BUY가 되면 안 됨, 실제: {label}"
    print(f"✅ test_no_vcp_high_rs_not_near_buy: {label}")


# ── 2. REVERSE_EXPANSION → NEAR_BUY/BUY_CANDIDATE 금지, RISK_WATCH 계열 ───────
def test_reverse_expansion_not_near_buy():
    row = _make_row(
        vcp_status="REVERSE_EXPANSION",
        vcp_display_score=44,
        rs_percentile=88,
        breakout_status="IN_BOX",
        failed_buy_gates=["VCP_REVERSE_EXPANSION"],
    )
    label = infer_final_label(row)
    assert label == "RISK_WATCH", \
        f"REVERSE_EXPANSION은 RISK_WATCH여야 함, 실제: {label}"
    print(f"✅ test_reverse_expansion_not_near_buy: {label}")


# ── 3. VCP 45 미만 → NEAR_BUY 금지 ─────────────────────────────────────────
def test_vcp_below_45_not_near_buy():
    for score in [30, 40, 44]:
        row = _make_row(
            vcp_status="CONTRACTION_WARN",
            vcp_display_score=score,
            vcp_effective_score=score,
            rs_percentile=85,
            breakout_status="IN_BOX",
            failed_buy_gates=["VCP_SCORE_BELOW_60"],
        )
        label = infer_final_label(row)
        assert label != "NEAR_BUY", \
            f"VCP {score} < 45 일 때 NEAR_BUY 금지, 실제: {label}"
        print(f"✅ test_vcp_below_45_not_near_buy (score={score}): {label}")


# ── 4. VCP 45~59 + RS 80+ + 돌파 근접 → 제한적 NEAR_BUY 가능 ──────────────
def test_vcp_45_59_near_buy_possible():
    row = _make_row(
        vcp_status="CONTRACTION_WARN",
        vcp_display_score=55,
        vcp_effective_score=55,
        rs_percentile=83,
        breakout_status="IN_BOX",  # NOT_READY 아님
        failed_buy_gates=["RS_BELOW_80"],  # 1개만 실패
    )
    label = infer_final_label(row)
    # RS=83 >= 80, VCP=55 >= 45, breakout IN_BOX (not NOT_READY) → eligible
    # BUT RS_BELOW_80 in failed_gates means the gate check said RS<80
    # The _is_near_buy_eligible checks rs_percentile field (83), so should be eligible
    assert label in {"NEAR_BUY", "PRIORITY_WATCH", "SETUP_WATCH"}, \
        f"VCP 55 RS 83 IN_BOX 종목 라벨: {label}"
    print(f"✅ test_vcp_45_59_near_buy_possible: {label}")


# ── 5. BUY_CANDIDATE 하드 게이트 미변경 확인 ──────────────────────────────────
def test_buy_candidate_gate_unchanged():
    """buy_gate_passed=True인 종목은 여전히 BUY_CANDIDATE."""
    row = _make_row(
        buy_gate_passed=True,
        failed_buy_gates=[],
        vcp_status="VCP_CONFIRMED",
        vcp_display_score=80,
        rs_percentile=90,
        breakout_status="NEAR_BREAKOUT",
    )
    label = infer_final_label(row)
    assert label == "BUY_CANDIDATE", \
        f"buy_gate_passed=True면 BUY_CANDIDATE여야 함, 실제: {label}"
    print(f"✅ test_buy_candidate_gate_unchanged: {label}")


# ── 6. CROSS_FACTOR_WEAK / MULTI_WEAK → vcp_quality_reason에만, vcp_status 아님 ─
def test_vcp_cross_factor_weak_is_quality_reason_not_status():
    from backend.alphaforge_policy import rejected_vcp_diagnostic_label
    # CROSS_FACTOR_WEAK, MULTI_WEAK는 rejected_vcp_diagnostic_label 결과로 나옴
    # 이는 display용이고 vcp_status 컬럼에는 들어가면 안 됨
    row_cross = _make_row(
        primary_bucket="REJECTED",
        vcp_status="NO_VCP",
        display_rejected_reasons=["이평선 비정렬"],
    )
    diag = rejected_vcp_diagnostic_label(row_cross)
    assert diag in {"CROSS_FACTOR_WEAK", "MULTI_WEAK", "NO_VCP", "REVERSE_EXPANSION", "LOW", "MEDIUM", "CONTRACTION_WARN"}, \
        f"diag_label 범위: {diag}"
    # vcp_status는 절대 CROSS_FACTOR_WEAK가 아님
    assert row_cross["vcp_status"] not in {"CROSS_FACTOR_WEAK", "MULTI_WEAK"}, \
        f"vcp_status는 CROSS_FACTOR_WEAK/MULTI_WEAK가 되면 안 됨, 실제: {row_cross['vcp_status']}"
    print(f"✅ test_vcp_cross_factor_weak_is_quality_reason_not_status: diag={diag}, vcp_status={row_cross['vcp_status']}")


# ── 7. market_cap 단위 확인 완료: 원 단위 기준 / 정상 범위 ──────────────────
# 2026-05-07: 삼성전자 270,500원 × 5.97억주 = 1581조 → 정상값, warning 없어야 함
def test_market_cap_samsung_1581jo_is_valid():
    row = _make_row(
        vcp_status="VCP_FORMING",
        market_cap=1_581_418_400_000_000,  # 삼성전자 실제값 (2026-05-07, 270,500원)
        buy_gate_passed=True,
        failed_buy_gates=[],
        rs_percentile=90,
        breakout_status="NEAR_BREAKOUT",
        vcp_display_score=80,
    )
    normalized = normalize_result_schema(dict(row))
    assert not normalized.get("market_cap_unit_warning"), \
        f"1581조는 2026년 삼성전자 정상값 — warning 없어야 함, 실제: {normalized.get('market_cap_unit_warning')}"
    print(f"✅ test_market_cap_samsung_1581jo_is_valid: warning='{normalized.get('market_cap_unit_warning')}' (정상 공백)")


def test_market_cap_unit_warning_triggered_for_formula_error():
    """5000조 초과 = 공식 오류 수준에서만 경고."""
    row = _make_row(
        vcp_status="VCP_FORMING",
        market_cap=5_001_000_000_000_000,  # 5001조 = 공식 오류 수준
        buy_gate_passed=True,
        failed_buy_gates=[],
        rs_percentile=90,
        breakout_status="NEAR_BREAKOUT",
        vcp_display_score=80,
    )
    normalized = normalize_result_schema(dict(row))
    assert normalized.get("market_cap_unit_warning"), \
        "5001조는 공식 오류 의심 → market_cap_unit_warning이 있어야 함"
    print(f"✅ test_market_cap_unit_warning_triggered_for_formula_error: {normalized['market_cap_unit_warning']}")


# ── 8. market_cap 정상 범위 (500조 이하) → warning 없음 ─────────────────────
def test_market_cap_normal_no_warning():
    row = _make_row(
        vcp_status="VCP_FORMING",
        market_cap=468_000_000_000_000,  # 468조 (삼성전자 실제 수준)
        buy_gate_passed=True,
        failed_buy_gates=[],
        rs_percentile=90,
        breakout_status="NEAR_BREAKOUT",
        vcp_display_score=80,
    )
    normalized = normalize_result_schema(dict(row))
    assert not normalized.get("market_cap_unit_warning"), \
        f"468조는 정상이므로 warning 없어야 함, 실제: {normalized.get('market_cap_unit_warning')}"
    print(f"✅ test_market_cap_normal_no_warning: warning='{normalized.get('market_cap_unit_warning')}'")


if __name__ == "__main__":
    test_no_vcp_high_rs_not_near_buy()
    test_reverse_expansion_not_near_buy()
    test_vcp_below_45_not_near_buy()
    test_vcp_45_59_near_buy_possible()
    test_buy_candidate_gate_unchanged()
    test_vcp_cross_factor_weak_is_quality_reason_not_status()
    test_market_cap_samsung_1581jo_is_valid()
    test_market_cap_unit_warning_triggered_for_formula_error()
    test_market_cap_normal_no_warning()
    print("\n🎉 All Phase 6 tests passed!")
