"""
AlphaForge Market Analysis Policy Engine.
Centralizes classification, alerting, and scoring rules to ensure consistency.

Design principles:
- pure functions: same input → same output, no symbol/name hardcoding
- preserve provided values; only fill missing fields with defaults
- reasons are always list[str] (no numpy/pandas leakage to display)
- score_max is run-level constant (210), not per-row inferred
"""
import pandas as pd
import numpy as np
from typing import Any, Dict, List, Tuple, Optional, Union

# AlphaForge 점수 체계 기본 분모. 모든 컴포넌트(VCP/box/RS/flow/macro/sector)의 합산은
# 0~210 사이로 설계되었으며, 동일 screening run 안에서는 210으로 고정한다.
DEFAULT_SCORE_MAX = 210.0

DISPLAY_LABELS = {
    "BUY_CANDIDATE",
    "NEAR_BUY",
    "PRIORITY_WATCH",
    "RISK_WATCH",
    "SETUP_WATCH",
    "REJECTED",
}

DISPLAY_LABEL_TEXT = {
    "BUY_CANDIDATE": "실전 매수 후보",
    "NEAR_BUY": "확인 필요 후보",
    "PRIORITY_WATCH": "최우선 관찰 후보",
    "RISK_WATCH": "리스크 관찰 후보",
    "SETUP_WATCH": "셋업 관찰 후보",
    "REJECTED": "제외",
}

BUY_ELIGIBLE_MODES = {"STRICT_MODE", "BUY_MODE"}
VCP_CONFIRMED_STATUSES = {"VCP_STRICT", "VCP_VALID", "VCP_CONFIRMED"}
VCP_FORMING_STATUSES = {"VCP_WARNING", "BASE_BUILDING", "HIGH_CONSOLIDATION", "NEAR_SETUP", "VCP_FORMING", "CONTRACTION_WARN"}
VCP_NO_VCP_STATUSES = {"NOT_READY", "NO_VCP"}
VCP_RISK_STATUSES = {"REVERSE_EXPANSION", "RALLY_EXHAUSTION"}

REASON_LABEL_MAP = {
    "HIGH_RS_CANDIDATE": "고RS 후보",
    "DATA_REVIEW_REQUIRED": "데이터 확인 필요",
    "RS_LEADERSHIP_ALIGNED": "RS 리더십 확인",
    "RISK_WATCH_REQUIRED": "리스크 관찰 필요",
    "ACTION_ALERT_REQUIRED": "실행 후보",
    "SETUP_WATCH_REQUIRED": "셋업 관찰 필요",
    "LIQUIDITY_UNCERTAIN": "유동성 확인 필요",
    "DATA_UNIT_WARNING": "데이터 단위 확인 필요",
    "REVERSE_EXPANSION": "VCP 역수축",
    "RALLY_EXHAUSTION": "랠리 과열/소진 관찰",
    "FAILED_BREAKOUT": "돌파 실패",
    "VCP_CONFIRMED": "VCP 확정",
    "VCP_FORMING": "VCP 형성 중",
    "CONTRACTION_WARN": "수축 품질 주의",
    "NO_VCP": "VCP 미형성",
    "BASE_BUILDING": "베이스 형성 관찰",
    "VCP_WARNING": "VCP 주의",
    "NOT_READY": "아직 준비 부족",
    "IN_BOX": "박스권 내부",
    "NEAR_BREAKOUT": "돌파 근접",
    "BREAKOUT_CONFIRMED": "돌파 확인",
    "HIGH_RS_RISK_WATCH": "고RS 리스크 관찰",
    "TIER_2_LEADERSHIP": "Tier 2 리더십 후보",
    "TIER_3_LEADER": "Tier 3 RS 리더",
    "NEAR_BREAKOUT_LEADER": "돌파 근접 RS 리더",
    "SETUP_WATCH_CANDIDATE": "셋업 관찰 후보",
    "REJECTED_BLOCK": "제외 종목 알림 차단",
    "LOW_PRIORITY": "우선순위 낮음",
    "LOW_RS_BLOCK": "RS 50 미만 차단",
    "REVERSE_EXPANSION_BLOCK": "VCP 역수축 차단",
    "FAILED_BREAKOUT_BLOCK": "돌파 실패 차단",
    "DATA_UNIT_WARNING_ACTION_BLOCK": "데이터 단위 확인 필요",
    "LIQUIDITY_UNCERTAIN_ACTION_BLOCK": "유동성 확인 필요",
    "RALLY_EXHAUSTION_RISK": "랠리 과열/소진 관찰",
}


def as_reason_list(value) -> List[str]:
    """Normalize reason values to list[str] without erasing existing reasons."""
    if value is None:
        return []
    try:
        import pandas as pd
        import numpy as np
        if value is pd.NA:
            return []
        if isinstance(value, float) and np.isnan(value):
            return []
        if isinstance(value, np.ndarray):
            return [str(x).strip() for x in value.tolist() if x is not None and str(x).strip() and str(x).strip() != "None"]
        if isinstance(value, pd.Series):
            return [str(x).strip() for x in value.dropna().tolist() if str(x).strip()]
    except Exception:
        pass

    if isinstance(value, list):
        return [str(x).strip() for x in value if x is not None and str(x).strip() and str(x).strip() != "None"]
    if isinstance(value, (tuple, set)):
        return [str(x).strip() for x in list(value) if x is not None and str(x).strip() and str(x).strip() != "None"]
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "없음" or text == "[]":
            return []
        if ";" in text:
            return [x.strip() for x in text.split(";") if x.strip()]
        return [text]

    text = str(value).strip()
    return [text] if text else []


def first_non_empty_reason_list(*values) -> List[str]:
    for value in values:
        items = as_reason_list(value)
        if items:
            return items
    return []


def dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items or []:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def normalize_reason_label(reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    return REASON_LABEL_MAP.get(text, text)


def _expand_reason_items(reasons: Any) -> List[str]:
    items: List[str] = []
    for item in as_reason_list(reasons):
        parts = [p.strip() for p in str(item).split(",") if p.strip()]
        items.extend(parts or [str(item).strip()])
    return items


def clean_display_reasons(reasons: Any, bucket: str = "") -> List[str]:
    bucket = str(bucket or "").upper()
    normalized = [normalize_reason_label(r) for r in _expand_reason_items(reasons)]
    normalized = [r for r in normalized if r and r != "없음"]
    out = dedupe_keep_order(normalized)

    if bucket == "TIER_3":
        out = ["구조적 안정성" if r == "주요 구조 안정적" else r for r in out]
        out = [r for r in out if r != "관찰 후보"]
    if bucket == "TIER_2":
        out = [r for r in out if r not in {"강한 주도주 후보", "확인 대기", "돌파 거래량 확인 대기"}]
    if "주요 구조 안정적" in out and "구조적 안정성" in out:
        out = [r for r in out if r != "주요 구조 안정적"]
    if "관찰 후보" in out and "Setup Watch" in out:
        remove = "Setup Watch" if bucket == "TIER_3" else "관찰 후보"
        out = [r for r in out if r != remove]
    if "RS 리더십 후보" in out and "강한 RS 리더십" in out:
        out = [r for r in out if r != "RS 리더십 후보"]

    return dedupe_keep_order(out)


def vcp_diagnostic_label(vcp_status: Any) -> str:
    status = str(vcp_status or "")
    if status in VCP_RISK_STATUSES or status in {"BASE_BUILDING", "NO_VCP", "VCP_CONFIRMED", "VCP_FORMING"}:
        return status
    if status in {"VCP_WARNING", "CONTRACTION_WARN"}:
        return "CONTRACTION_WARN"
    return "MEDIUM"


def rejected_vcp_diagnostic_label(row: Dict[str, Any]) -> str:
    if str(row.get("primary_bucket") or row.get("final_class") or "") != "REJECTED":
        return vcp_diagnostic_label(row.get("vcp_status"))
    reasons = set(get_display_rejected_reasons(row))
    has_ma_weak = "이평선 비정렬" in reasons
    has_reverse = "VCP 역수축" in reasons or str(row.get("vcp_status") or "") == "REVERSE_EXPANSION"
    if has_ma_weak and has_reverse:
        return "MULTI_WEAK"
    if has_reverse:
        return "REVERSE_EXPANSION"
    if has_ma_weak:
        return "CROSS_FACTOR_WEAK"
    return vcp_diagnostic_label(row.get("vcp_status"))


def build_tier2_display_reasons(row: Dict[str, Any]) -> List[str]:
    rs = safe_float(row.get("rs_percentile", row.get("rs_rating")))
    ma = str(row.get("ma_alignment") or row.get("ma_alignment_flag") or row.get("ma_status") or "")
    vcp_status = str(row.get("vcp_status") or "")
    flow = safe_float(row.get("flow_total_score") or row.get("flow_score") or row.get("supply_score"))
    reasons: List[str] = []
    if rs is not None and rs >= 85:
        reasons.append(f"RS 강한 리더십({rs:.1f})")
    elif rs is not None and rs >= 80:
        reasons.append(f"RS 리더십({rs:.1f})")
    if ma in {"ALIGNED", "정배열"}:
        reasons.append("정배열")
    if vcp_status in VCP_FORMING_STATUSES:
        reasons.append("VCP 수축 진행 중")
    if flow is not None and flow >= 20:
        reasons.append(f"수급 강함({flow:.0f})")
    return dedupe_keep_order(reasons)


def polish_display_reasons(row: Dict[str, Any], reasons: Any, bucket: str = "") -> List[str]:
    bucket = str(bucket or row.get("primary_bucket") or row.get("final_class") or "").upper()
    rs = safe_float(row.get("rs_percentile", row.get("rs_rating")))
    flow = safe_float(row.get("flow_total_score") or row.get("flow_score") or row.get("supply_score"))
    polished: List[str] = []
    for raw in _expand_reason_items(reasons):
        text = normalize_display_reason_text(raw, bucket)
        if text in {
            "확인 대기",
            "강한 주도주 후보",
            "관찰 후보",
            "주요 구조 안정적",
        }:
            continue
        replacements = {
            "VCP_WARNING 허용": "VCP 수축 진행 중",
            "LIQUIDITY_UNCERTAIN 허용 단 Tier 제한": "유동성 제약 내 구조 유효",
            "RALLY_EXHAUSTION 리스크 관찰": "추세 과열 진입 국면",
            "REVERSE_EXPANSION 관찰": "VCP 역수축 확인 중",
        }
        text = replacements.get(text, text)
        if text in {"강한 RS 리더십", "RS 리더십 후보", "RS 리더십", "고RS 후보"} and rs is not None:
            if rs >= 95:
                text = f"RS 최상위 리더십({rs:.1f})"
            elif rs >= 85:
                text = f"RS 강한 리더십({rs:.1f})"
            elif rs >= 80:
                text = f"RS 리더십({rs:.1f})"
        if text.startswith("수급 강함") and flow is not None and flow >= 20:
            text = f"수급 강함({flow:.0f})"
        if any(bad in text for bad in ("_WARNING", "_UNCERTAIN", "_허용")):
            continue
        polished.append(text)
    return clean_display_reasons(polished, bucket)


def normalize_display_reason_text(reason: Any, primary_bucket: str = "") -> str:
    text = str(reason or "").strip()
    bucket = str(primary_bucket or "").upper()
    if not text:
        return ""

    if bucket.startswith("TIER"):
        if text == "돌파 IN_BOX":
            return "돌파 거래량 확인 대기"
        if text.startswith("VCP 상태 부적합(") and text.endswith(")"):
            status = text.removeprefix("VCP 상태 부적합(").removesuffix(")")
            if status in VCP_FORMING_STATUSES | {"DATA_MISSING"}:
                return f"{status} 허용"
        if text.startswith("유동성 부적합(") and text.endswith(")"):
            status = text.removeprefix("유동성 부적합(").removesuffix(")")
            if status == "LIQUIDITY_UNCERTAIN":
                return "LIQUIDITY_UNCERTAIN 허용 단 Tier 제한"
            if status == "DATA_MISSING":
                return "유동성 데이터 확인 필요"
    return normalize_reason_label(text)


def normalize_display_reason_list(reasons: Any, primary_bucket: str = "") -> List[str]:
    normalized = [
        normalized
        for reason in _expand_reason_items(reasons)
        for normalized in [normalize_display_reason_text(reason, primary_bucket)]
        if normalized
    ]
    return clean_display_reasons(normalized, primary_bucket)


def extract_rejected_reasons_from_classification_text(text: Any) -> List[str]:
    label = str(text or "").strip()
    if not label or "REJECTED" not in label:
        return []
    tail = label.split("]", 1)[1] if "]" in label else label.split(":", 1)[-1]
    return clean_display_reasons([p.strip() for p in tail.split(",") if p.strip()], "REJECTED")


def build_feature_based_rejected_reasons(row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    rs = safe_float(row.get("rs_percentile", row.get("rs_rating")))
    ma = str(row.get("ma_alignment") or row.get("ma_alignment_flag") or row.get("ma_status") or "")
    flow = safe_float(row.get("supply_score", row.get("flow_total_score", row.get("flow_score"))))
    vcp_status = str(row.get("vcp_status") or "")
    breakout_status = str(row.get("breakout_status") or "")
    box_depth = safe_float(row.get("box_depth"))
    box_distance = safe_float(row.get("box_distance_pct", row.get("breakout_distance_pct")))
    liquidity_status = str(row.get("liquidity_status") or "")

    if rs is not None and rs < 50:
        reasons.append("RS 50 미만")
    if ma in {"NOT_ALIGNED", "비정렬", "BEARISH"}:
        reasons.append("이평선 비정렬")
    if flow is not None and flow <= 5:
        reasons.append("수급 극히 약함")
    if vcp_status == "REVERSE_EXPANSION":
        reasons.append("VCP 역수축")
    if vcp_status in VCP_NO_VCP_STATUSES:
        reasons.append("VCP 미형성")
    if breakout_status == "NOT_READY" and box_depth is not None and box_depth >= 20:
        reasons.append("박스권 매우 깊음")
    if (box_depth is not None and box_depth >= 20) or (box_distance is not None and abs(box_distance) >= 20):
        reasons.append("박스권 매우 깊음")
    if liquidity_status == "LIQUIDITY_UNCERTAIN":
        reasons.append("유동성 확인 필요")

    return clean_display_reasons(reasons, "REJECTED")


def get_display_rejected_reasons(row: Dict[str, Any]) -> List[str]:
    if str(row.get("primary_bucket", row.get("final_class", ""))) != "REJECTED":
        return []
    extracted = first_non_empty_reason_list(
        extract_rejected_reasons_from_classification_text(row.get("tier_reason")),
        extract_rejected_reasons_from_classification_text(row.get("candidate_reason")),
        extract_rejected_reasons_from_classification_text(row.get("classification_label")),
        extract_rejected_reasons_from_classification_text(row.get("description")),
        extract_rejected_reasons_from_classification_text(row.get("summary")),
        extract_rejected_reasons_from_classification_text(row.get("final_class")),
    )
    reasons = first_non_empty_reason_list(
        row.get("display_rejected_reasons"),
        row.get("rejected_reasons"),
        extracted,
        row.get("hard_gate_reasons"),
        row.get("risk_gate_reasons"),
        row.get("restriction_reasons"),
    )
    if not reasons:
        reasons = build_feature_based_rejected_reasons(row)
    return clean_display_reasons(reasons, "REJECTED")


def _first_present_value(source: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        try:
            missing = pd.isna(value)
            if isinstance(missing, (bool, np.bool_)) and missing:
                continue
        except Exception:
            pass
        return value
    return None


def _normalize_screening_mode(value: Any) -> str:
    mode = str(value or "").strip().upper()
    if mode in {"AND", "STRICT"}:
        return "STRICT_MODE"
    if mode in {"OR", "EXPLORE"}:
        return "EXPLORE_MODE"
    if mode == "BUY":
        return "BUY_MODE"
    return mode


def _is_ma_aligned(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text in {"ALIGNED", "TRUE", "YES", "Y", "1", "정배열"}:
        return True
    if text in {"NOT_ALIGNED", "FALSE", "NO", "N", "0", "역배열"}:
        return False
    return None


def get_buy_candidate_gate_result(
    candidate: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate hard gates for the reserved BUY_CANDIDATE display label."""
    context = context or {}
    failed: List[str] = []

    mode = _normalize_screening_mode(
        _first_present_value(candidate, "screening_mode", "screeningMode", "strategy_mode", "mode")
        or _first_present_value(context, "screening_mode", "screeningMode", "strategy_mode", "mode")
    )
    if not mode:
        failed.append("SCREENING_MODE_MISSING")
    elif mode not in BUY_ELIGIBLE_MODES:
        failed.append("SCREENING_MODE_NOT_STRICT")

    rs = safe_float(_first_present_value(candidate, "rs_percentile", "rs_rating", "rs_score", "rs", "rsRating", "rsScore"))
    if rs is None:
        failed.append("RS_MISSING")
    elif rs < 80:
        failed.append("RS_BELOW_80")

    vcp_score = safe_float(_first_present_value(
        candidate,
        "vcp_effective_score",
        "vcp_effective",
        "vcpEffective",
        "vcpEffectiveScore",
        "vcp_display_score",
        "vcp_display",
        "vcpDisplay",
        "vcpDisplayScore",
        "vcp_raw_score",
        "vcp_raw",
        "vcpRaw",
        "vcpRawScore",
        "vcp_score",
        "vcpScore",
    ))
    if vcp_score is None:
        failed.append("VCP_SCORE_MISSING")
    elif vcp_score < 60:
        failed.append("VCP_SCORE_BELOW_60")

    vcp_status = str(_first_present_value(candidate, "vcp_status", "vcpStatus", "vcp_diagnosis", "vcpDiagnosis") or "").upper()
    if not vcp_status:
        failed.append("VCP_STATUS_MISSING")
    elif vcp_status == "REVERSE_EXPANSION":
        failed.append("VCP_REVERSE_EXPANSION")

    ma_value = _first_present_value(
        candidate,
        "ma_alignment_flag",
        "ma_alignment",
        "ma_status",
        "ma_aligned",
        "is_ma_aligned",
        "movingAverageAligned",
        "moving_average_aligned",
    )
    ma_aligned = _is_ma_aligned(ma_value)
    if ma_aligned is None:
        failed.append("MA_ALIGNMENT_MISSING")
    elif not ma_aligned:
        failed.append("MA_NOT_ALIGNED")

    box_depth = safe_float(_first_present_value(candidate, "box_depth_pct", "boxDepthPct", "box_depth", "box_depth_percent"))
    if box_depth is None:
        box_depth = safe_float(_first_present_value(candidate, "breakout_distance_pct", "box_distance_pct", "boxDistancePct"))
    if box_depth is None:
        breakout_pct_for_depth = safe_float(_first_present_value(candidate, "box_breakout_pct", "breakout_pct", "boxBreakoutPct"))
        if breakout_pct_for_depth is not None:
            box_depth = abs(min(breakout_pct_for_depth, 0.0))
    if box_depth is None:
        failed.append("BOX_DEPTH_MISSING")
    elif box_depth > 20:
        failed.append("BOX_DEPTH_OVER_20")

    breakout_status = str(_first_present_value(candidate, "breakout_status", "breakoutStatus", "box_breakout_flag", "boxBreakoutFlag", "breakout_state") or "").upper()
    box_distance = safe_float(_first_present_value(candidate, "breakout_distance_pct", "box_distance_pct", "boxDistancePct"))
    valid_breakout_statuses = {"VALID_BREAKOUT", "BREAKOUT_CONFIRMED", "BREAKOUT_VALID"}
    pre_breakout_statuses = {"PRE_BREAKOUT", "NEAR_BREAKOUT", "HIGH_CONSOLIDATION", "IN_BOX_NEAR_PIVOT"}
    is_valid_breakout = breakout_status in valid_breakout_statuses
    is_pre_breakout = breakout_status in pre_breakout_statuses
    is_in_box_near = breakout_status == "IN_BOX" and box_distance is not None and box_distance <= 3.0
    if not breakout_status:
        failed.append("BREAKOUT_STATUS_MISSING")
    elif not (is_valid_breakout or is_pre_breakout or is_in_box_near):
        failed.append("BREAKOUT_STATUS_NOT_READY")

    if is_valid_breakout:
        volume_ratio = safe_float(_first_present_value(candidate, "volumeRatio", "volume_ratio", "breakout_volume_ratio", "breakoutVolumeRatio"))
        if volume_ratio is None:
            failed.append("VOLUME_RATIO_MISSING")
        elif volume_ratio < 1.5:
            failed.append("VOLUME_RATIO_BELOW_1_5")

    avg_trading_value = safe_float(_first_present_value(
        candidate,
        "liquidity_trading_value",
        "trading_value",
        "tradingValue",
        "raw_trading_value",
        "calculated_trading_value",
        "liquidity_avg_trading_value",
        "avg_trading_value_krw",
        "average_trading_value_krw",
        "avg_trading_value",
        "average_trading_value",
    ))
    min_trading_value = safe_float(
        _first_present_value(context, "min_trading_value_krw", "minTradingValueKrw")
        or _first_present_value(candidate, "min_trading_value_krw", "minTradingValueKrw")
    )
    if avg_trading_value is None:
        failed.append("AVG_TRADING_VALUE_MISSING")
    if min_trading_value is None:
        failed.append("MIN_TRADING_VALUE_MISSING")
    elif avg_trading_value is not None and avg_trading_value < min_trading_value:
        failed.append("AVG_TRADING_VALUE_BELOW_MIN")

    regime_raw = _first_present_value(context, "market_regime", "marketRegime", "dominant_regime", "dominantRegime")
    if isinstance(regime_raw, dict):
        regime_raw = regime_raw.get("dominant_regime") or regime_raw.get("dominantRegime")
    regime = str(regime_raw or _first_present_value(candidate, "dominant_regime", "dominantRegime", "market_regime", "marketRegime") or "").upper()
    if not regime:
        failed.append("MARKET_REGIME_MISSING")
    elif regime in {"RISK_OFF", "CRISIS"}:
        failed.append("MARKET_REGIME_RISK")

    data_quality = str(_first_present_value(candidate, "data_quality", "dataQuality", "data_quality_status", "dataQualityStatus", "data_unit_check", "dataUnitCheck", "data_status", "dataStatus") or "").upper()
    liquidity_status = str(_first_present_value(candidate, "liquidity_status") or "").upper()
    fatal_data_values = {"FATAL", "ERROR", "INVALID", "DATA_UNIT_WARNING", "DATA_MISSING"}
    if not data_quality:
        failed.append("DATA_QUALITY_MISSING")
    elif data_quality in fatal_data_values:
        failed.append("DATA_QUALITY_FATAL")
    if bool(candidate.get("data_unit_warning_flag")) or bool(candidate.get("vcp_data_missing")):
        failed.append("DATA_QUALITY_FATAL")
    if liquidity_status in {"LIQUIDITY_UNCERTAIN", "DATA_MISSING", "ILLIQUID"}:
        failed.append("DATA_QUALITY_FATAL")

    # Stable order with duplicates removed.
    failed = list(dict.fromkeys(failed))
    passed = not failed
    return {
        "buy_gate_passed": passed,
        "buyGatePassed": passed,
        "failed_buy_gates": failed,
        "failedBuyGates": failed,
        "buy_gate_reason": "PASS" if passed else ", ".join(failed),
        "buyGateReason": "PASS" if passed else ", ".join(failed),
        "screening_mode": mode,
        "screeningMode": mode,
    }


def evaluate_buy_candidate_gates(candidate: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return get_buy_candidate_gate_result(candidate, context)


def _is_near_buy_eligible(row: Dict[str, Any]) -> bool:
    """NEAR_BUY 최소 자격 조건.

    VCP 점수 44 이하이거나 NO_VCP 상태이거나 REVERSE_EXPANSION이면 NEAR_BUY 금지.
    RS < 80이면 NEAR_BUY 금지.
    breakout_status가 완전히 준비 안 된 상태(NOT_READY 계열)이면서 VCP도 낮으면 금지.
    """
    vcp_score = safe_float(
        row.get("vcp_display_score")
        or row.get("vcp_effective_score")
        or row.get("vcp_score")
        or 0
    )
    vcp_status = str(row.get("vcp_status") or "")
    rs = safe_float(row.get("rs_percentile") or row.get("rs_rating") or row.get("rs_score") or 0)
    failed_gates = as_reason_list(row.get("failed_buy_gates") or row.get("failedBuyGates"))
    breakout = str(row.get("breakout_status") or "")

    # VCP 30~44 또는 NO_VCP → NEAR_BUY 금지
    if vcp_status == "NO_VCP" or (vcp_score is not None and vcp_score < 45):
        return False
    # REVERSE_EXPANSION → NEAR_BUY 금지 (이미 risk_status에서 걸리지만 이중 방어)
    if vcp_status == "REVERSE_EXPANSION" or "VCP_REVERSE_EXPANSION" in failed_gates:
        return False
    # RS < 80 → NEAR_BUY 금지
    if rs is not None and rs < 80:
        return False
    # DATA_QUALITY_FATAL → 금지
    if "DATA_QUALITY_FATAL" in failed_gates:
        return False
    # breakout 완전 미준비(NOT_READY 계열) + VCP < 60 → PRIORITY_WATCH로
    breakout_not_ready = breakout in {"NOT_READY", "BREAKOUT_NOT_READY", "NO_BREAKOUT"}
    if breakout_not_ready and vcp_score is not None and vcp_score < 60:
        return False
    return True


def infer_final_label(row: Dict[str, Any]) -> str:
    """Map legacy tier/alert fields to user-facing AlphaForge labels."""
    explicit = str(
        row.get("final_label")
        or row.get("finalLabel")
        or row.get("display_label")
        or row.get("displayLabel")
        or row.get("display_watch_alert_type")
        or ""
    ).strip()
    if explicit in DISPLAY_LABELS and explicit != "BUY_CANDIDATE":
        return explicit

    bucket = str(row.get("primary_bucket", row.get("final_class", "")) or "").upper()
    if bucket == "REJECTED":
        return "REJECTED"

    raw_type = str(row.get("watch_alert_type") or "NONE")
    buy_gate_passed = bool(row.get("buy_gate_passed", row.get("buyGatePassed", False)))
    failed_buy_gates = as_reason_list(row.get("failed_buy_gates", row.get("failedBuyGates")))
    risk_status = (
        str(row.get("vcp_status") or "") in {"REVERSE_EXPANSION", "RALLY_EXHAUSTION"}
        or bool(row.get("vcp_rally_exhaustion_flag", False))
        or str(row.get("breakout_status") or "") == "FAILED_BREAKOUT"
        or str(row.get("liquidity_status") or "") == "LIQUIDITY_UNCERTAIN"
        or bool(row.get("data_unit_warning_flag", False))
        or str(row.get("data_unit_check") or "") == "DATA_UNIT_WARNING"
    )

    if risk_status:
        return "RISK_WATCH"
    if buy_gate_passed:
        if bucket == "TIER_3" and raw_type == "ACTION_ALERT":
            return "PRIORITY_WATCH"
        return "BUY_CANDIDATE"
    # NEAR_BUY: failed gates 1~2개 + VCP 최소 조건 충족 필요
    if 0 < len(failed_buy_gates) <= 2 and not (bucket == "TIER_3" and raw_type == "ACTION_ALERT"):
        if _is_near_buy_eligible(row):
            return "NEAR_BUY"
        # 조건 미충족: PRIORITY_WATCH 또는 SETUP_WATCH로 강등
        rs_val = safe_float(row.get("rs_percentile") or row.get("rs_rating") or 0) or 0
        return "PRIORITY_WATCH" if rs_val >= 80 else "SETUP_WATCH"

    if raw_type == "ACTION_ALERT":
        if bucket in {"TIER_1", "TIER_2"}:
            if _is_near_buy_eligible(row):
                return "NEAR_BUY"
            return "PRIORITY_WATCH"
        return "PRIORITY_WATCH"
    if raw_type in {"RISK_WATCH", "DATA_REVIEW"}:
        return "RISK_WATCH"
    if raw_type == "SETUP_WATCH":
        return "SETUP_WATCH"
    if not bool(row.get("watch_alert_flag", row.get("watchlist_flag", False))):
        if bucket in {"TIER_1", "TIER_2"}:
            if _is_near_buy_eligible(row):
                return "NEAR_BUY"
            rs_val = safe_float(row.get("rs_percentile") or row.get("rs_rating") or 0) or 0
            return "PRIORITY_WATCH" if rs_val >= 80 else "SETUP_WATCH"
        return "SETUP_WATCH"
    if bool(row.get("action_alert_flag", False)):
        if bucket in {"TIER_1", "TIER_2"}:
            if _is_near_buy_eligible(row):
                return "NEAR_BUY"
            return "PRIORITY_WATCH"
        return "PRIORITY_WATCH"
    if str(row.get("liquidity_status") or "") == "LIQUIDITY_UNCERTAIN" or bool(row.get("data_unit_warning_flag", False)):
        return "RISK_WATCH"
    if str(row.get("vcp_status") or "") in {"REVERSE_EXPANSION", "RALLY_EXHAUSTION"}:
        return "RISK_WATCH"
    if str(row.get("breakout_status") or "") == "FAILED_BREAKOUT":
        return "RISK_WATCH"
    return "SETUP_WATCH"



def infer_display_watch_alert_type(row: Dict[str, Any]) -> str:
    return infer_final_label(row)


def extract_display_reasons_from_classification_text(
    text: Any,
    primary_bucket: str = "",
) -> List[str]:
    """Extract UI promotion reasons from an existing classification label.

    This is intentionally a display adapter only: it does not create new
    classification logic or change scoring. It maps already-rendered labels like
    "WATCHLIST: [Setup Watch] 대형주 품질+보조강점, RS 리더십 근거로 추적"
    into a structured list for the UI.
    """
    bucket = str(primary_bucket or "").upper()
    if bucket == "REJECTED":
        return []

    label = str(text or "").strip()
    if not label:
        return []
    if label.upper() in {"TIER_1", "TIER_2", "TIER_3", "WATCHLIST", "CRISIS_HOLD", "REJECTED"}:
        return []

    reasons: List[str] = []

    bracket_map = {
        "실전 매수 후보": ["실전 매수 후보"],
        "강한 주도주 후보 / 확인 대기": ["강한 주도주 후보", "확인 대기"],
        "관찰 후보": ["관찰 후보"],
        "Setup Watch": ["Setup Watch"],
        "Risk Watch": ["Risk Watch"],
    }
    for marker, values in bracket_map.items():
        if f"[{marker}]" in label:
            reasons.extend(values)

    keyword_map = [
        "강한 주도주 후보",
        "확인 대기",
        "주요 구조 안정적",
        "대형주 품질",
        "보조강점",
        "수급 강함",
        "RS 리더십",
        "박스권 상단 근접",
    ]
    for keyword in keyword_map:
        if keyword in label:
            reasons.append(keyword)

    # Parse the human-readable reason section after the bracketed label.
    tail = label
    if "]" in tail:
        tail = tail.split("]", 1)[1]
    if "근거로 추적" in tail:
        tail = tail.split("근거로 추적", 1)[0]
    if "로 Tier 1 제한" in tail:
        tail = tail.split("로 Tier 1 제한", 1)[0]

    for chunk in tail.replace(":", ",").split(","):
        for part in chunk.split("+"):
            token = part.strip()
            if not token:
                continue
            if token in {"Tier 1", "Tier 2", "Tier 3", "TIER_1", "TIER_2", "TIER_3", "WATCHLIST"}:
                continue
            if token.startswith("[") or token.endswith("]"):
                continue
            reasons.append(normalize_display_reason_text(token, bucket))

    return normalize_display_reason_list(reasons, bucket)


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or (hasattr(value, "isna") and value.isna()):
            return None
        return float(value)
    except Exception:
        return None


def _is_na_scalar(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def normalize_score_fields(row: Dict[str, Any], run_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compute score_max / score_pct without breaking existing values.

    Policy:
    - run_context.score_max > row.score_max > AlphaForge default (210).
    - never infer score_max from total_score (95 must show as 95/210, not 95/100).
    """
    total_score = float(row.get("total_score", row.get("final_score", 0.0)) or 0.0)

    score_max = row.get("score_max", row.get("max_score", row.get("total_score_max")))
    if score_max is None or _is_na_scalar(score_max) or float(score_max) <= 0:
        # run-level override 우선
        if run_context and run_context.get("score_max"):
            score_max = float(run_context["score_max"])
        else:
            score_max = DEFAULT_SCORE_MAX
    else:
        score_max = float(score_max)

    score_pct = round((total_score / score_max) * 100, 2) if score_max > 0 else 0.0

    return {
        "total_score": total_score,
        "score_max": score_max,
        "score_pct": score_pct,
    }


def normalize_result_schema(row: Dict[str, Any], run_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Ensures all result rows have a consistent schema and normalized reason lists.

    NEVER overwrites a non-empty reason list with []. Only fills missing fields.
    """
    if row is None:
        return {}

    primary_bucket = str(row.get("primary_bucket", row.get("final_class", "REJECTED")))
    explicit_rejected_bucket = (
        row.get("primary_bucket") == "REJECTED"
        or row.get("candidate_status") == "REJECTED"
        or row.get("final_class") == "REJECTED"
        or str(row.get("tier_reason", "")).startswith("REJECTED:")
    )

    # 1. Reason alias 통합 — 항상 첫 번째 non-empty 사용 (덮어쓰기 금지)
    promotion = first_non_empty_reason_list(
        row.get("tier_promotion_reasons"),
        row.get("promotion_reasons"),
        row.get("upgrade_reasons"),
        row.get("tier_reasons"),
        row.get("classification_reasons"),
    )
    restriction = first_non_empty_reason_list(
        row.get("tier_restriction_reasons"),
        row.get("restriction_reasons"),
        row.get("limit_reasons"),
        row.get("blocked_reasons"),
    )
    downgrade = first_non_empty_reason_list(
        row.get("tier_downgrade_reasons"),
        row.get("downgrade_reasons"),
    )
    watchlist_reasons = first_non_empty_reason_list(
        row.get("watchlist_reasons"),
        row.get("retention_reasons"),
        row.get("setup_reasons"),
    )
    rejected = as_reason_list(row.get("rejected_reasons"))

    # 2. Display reasons — bucket별로 다른 우선순위
    label_reasons = []
    if primary_bucket in {"TIER_1", "TIER_2", "TIER_3", "WATCHLIST"}:
        label_reasons = first_non_empty_reason_list(
            extract_display_reasons_from_classification_text(row.get("tier_reason"), primary_bucket),
            extract_display_reasons_from_classification_text(row.get("candidate_reason"), primary_bucket),
            extract_display_reasons_from_classification_text(row.get("classification_label"), primary_bucket),
            extract_display_reasons_from_classification_text(row.get("description"), primary_bucket),
            extract_display_reasons_from_classification_text(row.get("summary"), primary_bucket),
            extract_display_reasons_from_classification_text(row.get("final_class"), primary_bucket),
        )

    if primary_bucket.startswith("TIER"):
        if primary_bucket == "TIER_2":
            display_promotion = build_tier2_display_reasons(row)
        else:
            display_promotion = first_non_empty_reason_list(
                normalize_display_reason_list(
                    label_reasons
                    + promotion
                    + build_feature_based_promotion_reasons(row),
                    primary_bucket,
                ),
                normalize_display_reason_list(row.get("display_promotion_reasons"), primary_bucket),
                normalize_display_reason_list(watchlist_reasons, primary_bucket),
            )
        if not display_promotion:
            display_promotion = build_feature_based_promotion_reasons(row)
        display_promotion = polish_display_reasons(row, display_promotion, primary_bucket)
            
        row["promotion_reasons"] = display_promotion
        row["tier_promotion_reasons"] = display_promotion
        row["display_promotion_reasons"] = display_promotion

    elif primary_bucket == "WATCHLIST":
        display_promotion = first_non_empty_reason_list(
            normalize_display_reason_list(
                label_reasons
                + watchlist_reasons
                + as_reason_list(row.get("display_promotion_reasons"))
                + as_reason_list(row.get("watch_alert_reasons")),
                primary_bucket,
            ),
            normalize_display_reason_list(promotion, primary_bucket),
        )
        if not display_promotion:
            display_promotion = build_feature_based_watchlist_reasons(row)
        display_promotion = polish_display_reasons(row, display_promotion, primary_bucket)
            
        row["display_promotion_reasons"] = display_promotion
        # WATCHLIST는 내부 promotion_reasons가 []일 수 있으나, display는 반드시 채움
        row.setdefault("promotion_reasons", [])
        row.setdefault("tier_promotion_reasons", [])

    else:
        # REJECTED 등은 promotion=[]
        display_promotion = []
        row["promotion_reasons"] = []
        row["tier_promotion_reasons"] = []
        row["display_promotion_reasons"] = []

    if primary_bucket == "REJECTED" and explicit_rejected_bucket:
        rejected = get_display_rejected_reasons({**row, "primary_bucket": primary_bucket})
        promotion = []
        display_promotion = []

    # 3. Score 정규화
    score_fields = normalize_score_fields(row, run_context)

    # 4. 필수 필드 기본값 (기존 값이 있으면 보존)
    schema_defaults = {
        "symbol": str(row.get("symbol", row.get("code", "000000"))),
        "code": str(row.get("code", row.get("symbol", "000000"))),
        "name": str(row.get("name", "Unknown")),
        "primary_bucket": primary_bucket,
        "final_class": str(row.get("final_class", primary_bucket)),
        "rs_percentile": float(row.get("rs_percentile", row.get("rs_rating", 0.0)) or 0.0),
        "vcp_score": float(row.get("vcp_score", 0.0) or 0.0),
        "vcp_raw_score": float(row.get("vcp_raw_score", row.get("vcp_score", 0.0)) or 0.0),
        "vcp_effective_score": float(row.get("vcp_effective_score", row.get("vcp_score", 0.0)) or 0.0),
        "vcp_display_score": float(row.get("vcp_display_score", row.get("vcp_score", 0.0)) or 0.0),
        "vcp_status": str(row.get("vcp_status", "DATA_MISSING")),
        "vcp_confidence": str(row.get("vcp_confidence", "LOW")),
        "vcp_cross_warning": row.get("vcp_cross_warning") or "",
        "candidate_confidence": str(row.get("candidate_confidence", "WEAK")),
        "watch_alert_flag": bool(row.get("watch_alert_flag", row.get("watchlist_flag", False))),
        "watch_alert_type": str(row.get("watch_alert_type", "NONE")),
        "legacy_label": str(row.get("legacy_label", row.get("watch_alert_type", row.get("alert_type", "NONE")))),
        "legacyLabel": str(row.get("legacyLabel", row.get("legacy_label", row.get("watch_alert_type", row.get("alert_type", "NONE"))))),
        "action_alert_flag": bool(row.get("action_alert_flag", False)),
        "data_unit_warning_flag": bool(row.get("data_unit_warning_flag", False)),
        "liquidity_status": str(row.get("liquidity_status", "ILLIQUID")),
    }

    # 5. Update Row (덮어쓰기 안전)
    row.update(schema_defaults)
    row.update(score_fields)

    # reason 계열은 항상 list[str]로 통일 (덮어쓰기는 빈 리스트로만 덮지 않게 보존)
    row["tier_promotion_reasons"] = promotion
    row["promotion_reasons"] = promotion
    row["tier_restriction_reasons"] = restriction
    row["restriction_reasons"] = restriction
    row["tier_downgrade_reasons"] = downgrade
    row["downgrade_reasons"] = downgrade
    row["watchlist_reasons"] = watchlist_reasons
    row["retention_reasons"] = watchlist_reasons
    row["setup_reasons"] = watchlist_reasons
    row["rejected_reasons"] = rejected
    row["display_promotion_reasons"] = display_promotion
    row["display_rejected_reasons"] = rejected if primary_bucket == "REJECTED" else []
    row["display_restriction_reasons"] = normalize_display_reason_list(restriction, primary_bucket)

    row["risk_gate_reasons"] = as_reason_list(row.get("risk_gate_reasons"))
    watch_alert_reasons_raw = as_reason_list(row.get("watch_alert_reasons"))
    watch_alert_exclusion_raw = as_reason_list(row.get("watch_alert_exclusion_reasons"))
    row["watch_alert_reasons_raw"] = watch_alert_reasons_raw
    row["watch_alert_exclusion_reasons_raw"] = watch_alert_exclusion_raw
    row["watch_alert_reasons"] = watch_alert_reasons_raw
    row["watch_alert_reasons_display"] = normalize_display_reason_list(watch_alert_reasons_raw, primary_bucket)
    row["display_watch_alert_reasons"] = row["watch_alert_reasons_display"]
    row["watch_alert_exclusion_reasons"] = watch_alert_exclusion_raw
    row["watch_alert_exclusion_reasons_display"] = normalize_display_reason_list(watch_alert_exclusion_raw, primary_bucket)
    if "buy_gate_passed" not in row and "buyGatePassed" not in row:
        row.update(get_buy_candidate_gate_result(row, run_context or {}))
    else:
        failed_buy_gates = as_reason_list(row.get("failed_buy_gates", row.get("failedBuyGates")))
        row["failed_buy_gates"] = failed_buy_gates
        row["failedBuyGates"] = failed_buy_gates
        row["buy_gate_reason"] = str(row.get("buy_gate_reason", row.get("buyGateReason", "")))
        row["buyGateReason"] = str(row.get("buyGateReason", row.get("buy_gate_reason", "")))
    final_label = infer_final_label(row)
    row["display_watch_alert_type"] = final_label
    row["display_label"] = final_label
    row["displayLabel"] = final_label
    row["final_label"] = final_label
    row["finalLabel"] = final_label
    row["display_label_text"] = DISPLAY_LABEL_TEXT.get(final_label, final_label)
    row["final_label_text"] = DISPLAY_LABEL_TEXT.get(final_label, final_label)
    row["display_watch_alert_label"] = build_display_fields({**row, "watch_alert_type": row["display_watch_alert_type"]}).get("alert_display", "")
    row["watch_alert_decision_trace"] = str(row.get("watch_alert_decision_trace", ""))
    row["vcp_penalty_reasons"] = as_reason_list(row.get("vcp_penalty_reasons"))
    row["vcp_bonus_reasons"] = as_reason_list(row.get("vcp_bonus_reasons"))
    row["vcp_cross_warning"] = as_reason_list(row.get("vcp_cross_warning")) if isinstance(row.get("vcp_cross_warning"), (list, tuple, np.ndarray)) else (
        [str(row.get("vcp_cross_warning"))] if row.get("vcp_cross_warning") else []
    )

    # display 문자열 (CSV/clipboard용)
    row["promotion_reasons_str"] = "; ".join(promotion)
    row["display_promotion_reasons_str"] = "; ".join(display_promotion)
    row["watchlist_reasons_str"] = "; ".join(watchlist_reasons)
    row["rejected_reasons_str"] = "; ".join(rejected)
    row["display_rejected_reasons_str"] = "; ".join(row["display_rejected_reasons"])
    row["display_restriction_reasons_str"] = "; ".join(row["display_restriction_reasons"])
    row["display_watch_alert_reasons_str"] = "; ".join(row["display_watch_alert_reasons"])
    row["failed_buy_gates_str"] = "; ".join(row.get("failed_buy_gates", []))

    # vcp_diagnostic: status와 quality_reason 명확히 분리
    _vcp_status = str(row.get("vcp_status") or "DATA_MISSING")
    _vcp_diag_label = rejected_vcp_diagnostic_label(row)
    # CROSS_FACTOR_WEAK / MULTI_WEAK는 quality_reason에만 속함 (vcp_status로 혼용 방지)
    _vcp_quality_reason = str(row.get("vcp_quality_reason") or "")
    _cross_warn = row.get("vcp_cross_warning", [])
    _cross_str = "; ".join(_cross_warn) if _cross_warn else ""
    row["vcp_diagnostic"] = (
        f"raw {row.get('vcp_raw_score')} → effective {row.get('vcp_effective_score')} → display {row.get('vcp_display_score')}"
        f" | status={_vcp_status}"
        + (f" | quality_reason={_vcp_diag_label}" if _vcp_diag_label not in (_vcp_status, "NONE") else "")
        + (f" | cross={_cross_str}" if _cross_str else "")
    )

    # market_cap 단위 과장 경고 (한국 주식 기준 1000조 초과 시 단위 의심)
    # market_cap 단위 확인 완료: 원 단위 (KRW) 기준.
    # naver_krx.py: marketValue(억원) × 1억 → 원 단위. formatCap: ÷1e12 → 조 표시.
    # 2026-05-07 기준 삼성전자 270,500원 × 5.97억주 ≈ 1581조, 정상값.
    # 경고 임계: 5000조 초과 = 실제 단위 오류 수준에서만 경고 (정상 주가 상승분 오탐 방지)
    _mcap = safe_float(row.get("market_cap"))
    _MCAP_KR_SUSPICIOUS_THRESHOLD = 5_000_000_000_000_000  # 5000조 (원 단위, 공식 오류 탐지용)
    if _mcap and _mcap > _MCAP_KR_SUSPICIOUS_THRESHOLD:
        row["market_cap_unit_warning"] = (
            f"시총 {_mcap/1e12:.0f}조 공식 오류 의심 (raw={_mcap:.2e})"
        )
        row["data_unit_warning_flag"] = row.get("data_unit_warning_flag", False)  # 기존 플래그는 유지
    elif not row.get("market_cap_unit_warning"):
        row["market_cap_unit_warning"] = ""

    return row



def build_display_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Compute UI-friendly fields for cards/CSV/clipboard. Pure function."""
    bucket = str(row.get("primary_bucket", "REJECTED"))
    alert_type = infer_final_label(row)

    bucket_label_map = {
        "TIER_1": "Tier 1 실전 매수",
        "TIER_2": "Tier 2 주도주 후보",
        "TIER_3": "Tier 3 관찰 후보",
        "WATCHLIST": "Watchlist 추적",
        "CRISIS_HOLD": "Crisis Hold 보류",
        "REJECTED": "Rejected 제외",
    }
    alert_emoji_map = {
        "BUY_CANDIDATE": "◎",
        "NEAR_BUY": "○",
        "PRIORITY_WATCH": "★",
        "RISK_WATCH": "⚠️",
        "SETUP_WATCH": "◇",
        "REJECTED": "",
        "ACTION_ALERT": "★",
        "DATA_REVIEW": "⚠️",
        "NONE": "",
    }
    alert_label_map = {
        "BUY_CANDIDATE": "BUY CANDIDATE",
        "NEAR_BUY": "NEAR BUY",
        "PRIORITY_WATCH": "PRIORITY WATCH",
        "RISK_WATCH": "RISK WATCH",
        "SETUP_WATCH": "SETUP WATCH",
        "REJECTED": "REJECTED",
        "ACTION_ALERT": "PRIORITY WATCH",
        "DATA_REVIEW": "RISK WATCH",
        "NONE": "",
    }

    return {
        "bucket_display": bucket_label_map.get(bucket, bucket),
        "display_label": alert_type,
        "display_label_text": DISPLAY_LABEL_TEXT.get(alert_type, alert_type),
        "final_label": alert_type,
        "final_label_text": DISPLAY_LABEL_TEXT.get(alert_type, alert_type),
        "alert_emoji": alert_emoji_map.get(alert_type, ""),
        "alert_label": alert_label_map.get(alert_type, ""),
        "alert_display": (
            f"{alert_emoji_map.get(alert_type, '')} [{alert_label_map.get(alert_type, '')}]"
            if alert_type and alert_type not in {"NONE", "REJECTED"} and bool(row.get("watch_alert_flag"))
            else ""
        ),
    }


def build_ai_system_prompt(as_of_date: str = "", signal_names: List[str] = None) -> str:
    """Builds a system prompt for the AI analysis node."""
    signals = ", ".join(signal_names) if signal_names else "Technical factors"
    return f"""
    You are an expert market analyst focusing on VCP (Volatility Contraction Pattern) and RS (Relative Strength).
    Analysis Date: {as_of_date}
    Focus Signals: {signals}

    Task: Review the provided stock data and provide a concise technical commentary, grade (A-F), and confidence score (0-100).
    Follow the AlphaForge policy: prioritize RS leadership and tight VCP structures.
    """


default_stock_analysis_prompt = """
Review the stock's VCP pattern, RS percentile, and liquidity.
Identify if the stock is a 'Leader' or 'Laggard'.
Provide a concise technical grade.
"""


def classify_primary_bucket(row: Dict[str, Any]) -> Tuple[str, str, List[str], List[str], List[str]]:
    """Classifies a stock into TIER_1, TIER_2, TIER_3, WATCHLIST, or REJECTED.

    Returns: (bucket, reason, rejected_reasons, quality_factors, t1_restrictions)
    Side effect: sets row["_t2_restrictions"] = list (internal diagnostic).
    """
    rs_val = float(row.get("rs_percentile", row.get("rs_rating", 0)) or 0)
    rs_status = str(row.get("rs_status", ""))
    vcp_status = str(row.get("vcp_status", ""))
    vcp_warn = str(row.get("vcp_warning", ""))
    breakout_status = str(row.get("breakout_status", ""))
    breakout_grade = str(row.get("box_breakout_grade", ""))
    box_warn = str(row.get("box_breakout_warning", ""))
    ma_flag = str(row.get("ma_alignment_flag", ""))
    liquidity_status = str(row.get("liquidity_status", ""))
    dist_val = float(row.get("breakout_distance_pct", 5.0) or 5.0)
    flow_total = float(row.get("flow_total_score", 0) or 0)
    total = float(row.get("total_score", 0) or 0)

    is_rs_80 = rs_val >= 80 or rs_status == "Strong"
    is_rs_60 = rs_val >= 60
    is_rs_50 = rs_val >= 50
    rs_data_missing = rs_status == "DATA_MISSING" or pd.isna(row.get("rs_rating"))
    ma_data_missing = ma_flag == "DATA_MISSING"
    is_ma_aligned = ma_flag in {"ALIGNED", "DATA_MISSING"}
    is_liquid = liquidity_status == "LIQUID"

    rejected_reasons = []
    if rs_val < 50 and rs_status not in {"Strong", "DATA_MISSING"}: rejected_reasons.append("RS 50 미만")
    if vcp_status in VCP_NO_VCP_STATUSES: rejected_reasons.append(f"VCP 미형성({vcp_status})")
    if breakout_status == "FAILED_BREAKOUT": rejected_reasons.append("돌파 실패(FAILED_BREAKOUT)")
    if vcp_status == "REVERSE_EXPANSION" or "역수축" in vcp_warn: rejected_reasons.append("VCP 역수축")
    if flow_total <= 5: rejected_reasons.append("수급 극히 약함")
    if ma_flag == "NOT_ALIGNED": rejected_reasons.append("이평선 비정렬")
    if dist_val > 15.0: rejected_reasons.append("박스권 매우 깊음")
    if liquidity_status == "ILLIQUID": rejected_reasons.append("거래대금/유동성 Hard Gate")

    quality_factors = []
    if rs_val >= 80: quality_factors.append("RS 리더십")
    if flow_total >= 25: quality_factors.append("수급 강함")
    if dist_val <= 3.0: quality_factors.append("박스권 상단 근접")
    if is_ma_aligned and is_liquid: quality_factors.append("대형주 품질")

    t1_restrictions = []
    if vcp_status == "DATA_MISSING" or row.get("vcp_data_missing"): t1_restrictions.append("VCP 데이터 부족")
    if rs_data_missing: t1_restrictions.append("RS 데이터 부족")
    elif rs_val < 80 and rs_status != "Strong": t1_restrictions.append("RS 80 미달")
    if not is_liquid: t1_restrictions.append(f"유동성 부적합({liquidity_status})")
    if not is_ma_aligned: t1_restrictions.append("이평선 비정렬")
    if breakout_status not in {"BREAKOUT_CONFIRMED", "NEAR_BREAKOUT", "HIGH_CONSOLIDATION"}:
        t1_restrictions.append(f"돌파 {breakout_status}")
    if vcp_status not in VCP_CONFIRMED_STATUSES:
        t1_restrictions.append(f"VCP 상태 부적합({vcp_status})")
    if dist_val > 7.0: t1_restrictions.append(f"박스권 깊음({dist_val:.1f}%)")
    if "거래량 부족" in box_warn or (breakout_status == "BREAKOUT_CONFIRMED" and "D" in breakout_grade):
        t1_restrictions.append("돌파 거래량 부족")

    t2_restrictions = []
    if vcp_status == "REVERSE_EXPANSION": t2_restrictions.append("VCP 역수축")
    if vcp_status in VCP_NO_VCP_STATUSES: t2_restrictions.append("VCP 미형성")
    if breakout_status == "FAILED_BREAKOUT": t2_restrictions.append("돌파 실패 상태")
    if rs_val < 50 and rs_status not in {"Strong", "DATA_MISSING"}: t2_restrictions.append("RS 저조")
    if ma_flag == "NOT_ALIGNED": t2_restrictions.append("이평선 역배열")
    if flow_total <= 5: t2_restrictions.append("수급 약함")
    if liquidity_status in {"ILLIQUID", "DATA_MISSING"}: t2_restrictions.append("유동성 부족/누락")
    # 박스권 깊이 — 7% 초과는 TIER_2 자격 박탈
    if dist_val > 7.0: t2_restrictions.append(f"박스권 깊음({dist_val:.1f}%)")

    # row에 진단용 보관 (호출자에서 t2_rejection_reasons로 사용 가능)
    row["_t2_restrictions"] = t2_restrictions

    bucket = "WATCHLIST"
    reason = ""

    is_rejected = len(rejected_reasons) >= 3 or (len(rejected_reasons) >= 2 and not quality_factors)
    if is_rejected and rs_val >= 80 and len(rejected_reasons) < 4:
        is_rejected = False  # RS 리더십으로 구제

    if is_rejected:
        bucket = "REJECTED"
        reason = f"REJECTED: [복합 약점] {', '.join(rejected_reasons)}"
    elif (is_rs_80 and is_liquid and is_ma_aligned and
          breakout_status in {"BREAKOUT_CONFIRMED", "NEAR_BREAKOUT", "HIGH_CONSOLIDATION"} and
          vcp_status in VCP_CONFIRMED_STATUSES and not t1_restrictions):
        bucket = "TIER_1"
        reason = "Tier 1: [실전 매수 후보] 핵심 필터 모두 통과"
    elif (is_rs_80 and is_liquid and is_ma_aligned and
          breakout_status in {"IN_BOX", "NEAR_BREAKOUT", "BREAKOUT_CONFIRMED", "HIGH_CONSOLIDATION"} and
          vcp_status in VCP_CONFIRMED_STATUSES | VCP_FORMING_STATUSES and not t2_restrictions):
        bucket = "TIER_2"
        reason = f"Tier 2: [강한 주도주 후보 / 확인 대기] {', '.join(t1_restrictions[:2])}로 Tier 1 제한"
    elif (not rejected_reasons and (is_rs_80 or total >= 120)):
        bucket = "TIER_3"
        reason = "Tier 3: [관찰 후보] 주요 구조 안정적"
    else:
        valid_watch_reasons = []
        if "대형주 품질" in quality_factors:
            if is_rs_60 or flow_total >= 20 or dist_val <= 7.0 or vcp_status in VCP_CONFIRMED_STATUSES | VCP_FORMING_STATUSES or is_ma_aligned:
                valid_watch_reasons.append("대형주 품질+보조강점")
        if "수급 강함" in quality_factors:
            if is_rs_50 or dist_val <= 10.0 or is_ma_aligned or vcp_status in VCP_CONFIRMED_STATUSES | VCP_FORMING_STATUSES:
                valid_watch_reasons.append("수급 강함+보조강점")
        if "RS 리더십" in quality_factors: valid_watch_reasons.append("RS 리더십")
        if "박스권 상단 근접" in quality_factors: valid_watch_reasons.append("박스권 상단 근접")

        if not valid_watch_reasons and not quality_factors:
            bucket = "REJECTED"
            reason = "REJECTED: [잠재력 부족] 조건 미달 및 구조 불안정"
        elif not valid_watch_reasons:
            bucket = "REJECTED"
            reason = f"REJECTED: [추적 가치 부족] {quality_factors[0]} 단독 사유만 존재"
        else:
            bucket = "WATCHLIST"
            # WATCHLIST 라벨: 결함 사유가 있는데 RS 리더십으로 구제된 경우 Risk Watch
            has_reverse = vcp_status == "REVERSE_EXPANSION" or "역수축" in vcp_warn
            has_rally = vcp_status == "RALLY_EXHAUSTION" or bool(row.get("vcp_rally_exhaustion_flag", False))
            has_failed_breakout = breakout_status == "FAILED_BREAKOUT"
            if has_reverse and rs_val >= 80:
                reason = (
                    f"WATCHLIST: [Risk Watch] 역수축 변동성 확장 — RS 리더십({rs_val:.0f})으로 추적 유지: "
                    f"{', '.join(valid_watch_reasons)}"
                )
            elif has_rally and rs_val >= 80:
                reason = (
                    f"WATCHLIST: [Risk Watch] 랠리 피로도 — RS 리더십으로 추적 유지: "
                    f"{', '.join(valid_watch_reasons)}"
                )
            elif has_failed_breakout and rs_val >= 80:
                reason = (
                    f"WATCHLIST: [Risk Watch] 돌파 실패 — RS 리더십으로 추적 유지: "
                    f"{', '.join(valid_watch_reasons)}"
                )
            else:
                reason = f"WATCHLIST: [Setup Watch] {', '.join(valid_watch_reasons)} 근거로 추적"

    return bucket, reason, rejected_reasons, quality_factors, t1_restrictions


def build_promotion_reasons(
    bucket: str,
    quality_factors: List[str],
    t1_restrictions: List[str],
    rejected_reasons: List[str],
    row: Dict[str, Any],
) -> Tuple[List[str], List[str]]:
    """Build (promotion_reasons, watchlist_reasons) lists for the row's bucket.

    TIER_1/2/3:
        - quality_factors가 promotion의 핵심
        - "확인 대기" / "RS 리더십 후보" / "정배열 + 박스권 허용 범위" / VCP 상태별 라벨 추가
    WATCHLIST:
        - watchlist_reasons에 quality + 셋업 근거
        - promotion_reasons는 빈 list (티어 승격 사유는 아니기 때문)
    REJECTED:
        - 둘 다 빈 list
    """
    rs_val = float(row.get("rs_percentile", row.get("rs_rating", 0)) or 0)
    vcp_status = str(row.get("vcp_status", ""))
    breakout_status = str(row.get("breakout_status", ""))
    flow_total = float(row.get("flow_total_score", 0) or 0)
    ma_flag = str(row.get("ma_alignment_flag", ""))
    liquidity_status = str(row.get("liquidity_status", ""))
    dist_val = float(row.get("breakout_distance_pct", 5.0) or 5.0)

    promotion_reasons: List[str] = []
    watchlist_reasons: List[str] = []

    if bucket in {"TIER_1", "TIER_2", "TIER_3"}:
        # 1) Tier 라벨 (의미 있는 promotion 헤더)
        if bucket == "TIER_1":
            promotion_reasons.append("실전 매수 후보")
        elif bucket == "TIER_2":
            promotion_reasons.append("강한 주도주 후보")
            if t1_restrictions:
                promotion_reasons.append("확인 대기")
        else:  # TIER_3
            promotion_reasons.append("관찰 후보")

        # 2) Quality factors
        if "RS 리더십" in quality_factors:
            promotion_reasons.append("RS 리더십 후보")
        if "수급 강함" in quality_factors:
            promotion_reasons.append("수급 강함")
        if "박스권 상단 근접" in quality_factors:
            promotion_reasons.append("박스권 상단 근접")
        if "대형주 품질" in quality_factors:
            promotion_reasons.append("대형주 품질")

        # 3) 구조 강점
        if ma_flag == "ALIGNED" and dist_val <= 7.0:
            promotion_reasons.append("정배열 + 박스권 허용 범위")
        if vcp_status in VCP_CONFIRMED_STATUSES:
            promotion_reasons.append(f"VCP 정상({vcp_status})")
        elif vcp_status in VCP_FORMING_STATUSES:
            promotion_reasons.append(f"{vcp_status} 허용")
        if breakout_status == "BREAKOUT_CONFIRMED":
            promotion_reasons.append("돌파 확정")
        elif breakout_status == "NEAR_BREAKOUT":
            promotion_reasons.append("돌파 임박")
        elif breakout_status == "IN_BOX":
            promotion_reasons.append("돌파 거래량 확인 대기")
        if liquidity_status == "LIQUID":
            promotion_reasons.append("유동성 충족")

    elif bucket == "WATCHLIST":
        # WATCHLIST에는 retention/setup reason을 모음
        if quality_factors:
            for q in quality_factors:
                watchlist_reasons.append(q)
        if rs_val >= 80:
            watchlist_reasons.append("RS 리더십 유지")
        elif rs_val >= 60:
            watchlist_reasons.append("RS 보통+")
        if vcp_status in VCP_CONFIRMED_STATUSES | VCP_FORMING_STATUSES:
            watchlist_reasons.append(f"베이스 형성({vcp_status})")
        if dist_val <= 5.0:
            watchlist_reasons.append("박스권 상단 근접")
        if flow_total >= 15:
            watchlist_reasons.append("수급 양호")
        if ma_flag == "ALIGNED":
            watchlist_reasons.append("정배열 유지")
        if liquidity_status == "LIQUID" and not watchlist_reasons:
            # 유동성만 있는 경우라도 최소 한 개 사유 보장
            watchlist_reasons.append("유동성 충족")
        if not watchlist_reasons:
            watchlist_reasons.append("관찰 유지")

    # REJECTED: 둘 다 빈 리스트로 둠 (rejected_reasons는 caller에서 전달)

    # 중복 제거 (순서 보존)
    promotion_reasons = list(dict.fromkeys(promotion_reasons))
    watchlist_reasons = list(dict.fromkeys(watchlist_reasons))

    return promotion_reasons, watchlist_reasons


def build_feature_based_promotion_reasons(row: Dict[str, Any]) -> List[str]:
    reasons = []
    bucket = str(row.get("primary_bucket") or row.get("final_class") or "")
    rs = safe_float(row.get("rs_percentile"))
    vcp_status = str(row.get("vcp_status") or "")
    ma = str(row.get("ma_alignment") or row.get("ma_alignment_flag") or row.get("ma_status") or "")
    breakout = str(row.get("breakout_status") or "")
    liquidity = str(row.get("liquidity_status") or "")
    supply = safe_float(row.get("flow_total_score") or row.get("flow_score") or row.get("supply_score"))

    if rs is not None and rs >= 90:
        reasons.append("강한 RS 리더십")
    elif rs is not None and rs >= 80:
        reasons.append("RS 리더십 후보")

    if ma in {"ALIGNED", "정배열"}:
        reasons.append("정배열")

    if vcp_status in VCP_FORMING_STATUSES:
        reasons.append(f"{vcp_status} 허용")
    elif vcp_status in VCP_CONFIRMED_STATUSES:
        reasons.append("VCP 구조 양호")
    elif vcp_status == "RALLY_EXHAUSTION":
        reasons.append("RALLY_EXHAUSTION 리스크 관찰")

    if breakout in {"IN_BOX", "NEAR_BREAKOUT"}:
        reasons.append("확인 대기")

    if liquidity == "LIQUIDITY_UNCERTAIN":
        reasons.append("LIQUIDITY_UNCERTAIN 허용 단 Tier 제한")

    if supply is not None and supply >= 25:
        reasons.append("수급 보조 강점")

    if bucket == "TIER_3":
        reasons.append("구조적 안정성")
        reasons.append("관찰 후보")

    if bucket == "TIER_2":
        reasons.append("강한 주도주 후보")
        reasons.append("확인 대기")

    return dedupe_keep_order(reasons)


def build_feature_based_watchlist_reasons(row: Dict[str, Any]) -> List[str]:
    reasons = []
    rs = safe_float(row.get("rs_percentile"))
    vcp_status = str(row.get("vcp_status") or "")
    watch_type = str(row.get("watch_alert_type") or "")
    supply = safe_float(row.get("flow_total_score") or row.get("flow_score") or row.get("supply_score"))
    box_distance = safe_float(row.get("breakout_distance_pct") or row.get("box_distance_pct"))
    box_depth = safe_float(row.get("box_depth"))

    # large_cap_quality 필드 호환성
    large_cap_quality = bool(row.get("large_cap_quality") or row.get("is_large_cap") or row.get("mega_cap_quality"))
    # 시총 기반 추정 (옵션)
    mcap = safe_float(row.get("market_cap"))
    if not large_cap_quality and mcap:
        if mcap >= 1_000_000_000_000: # 1조 이상 (단위가 원인 경우)
             large_cap_quality = True
        elif mcap >= 10_000: # 1조 이상 (단위가 억원인 경우)
             large_cap_quality = True

    if rs is not None and rs >= 90:
        reasons.append("강한 RS 리더십")
    elif rs is not None and rs >= 80:
        reasons.append("RS 리더십")

    if large_cap_quality:
        reasons.append("대형주 품질")

    if supply is not None and supply >= 25:
        reasons.append("수급 강함")

    if box_distance is not None and box_distance >= -5:
        reasons.append("박스권 상단 근접")
    elif box_depth is not None and box_depth <= 10:
        reasons.append("박스권 상단 근접")

    if watch_type == "RISK_WATCH" or vcp_status in {"REVERSE_EXPANSION", "RALLY_EXHAUSTION"} or bool(row.get("vcp_rally_exhaustion_flag", False)):
        reasons.append("RISK_WATCH 대상")
    elif watch_type == "DATA_REVIEW":
        reasons.append("DATA_REVIEW 대상")
    else:
        reasons.append("Setup Watch")

    return dedupe_keep_order(reasons)


def build_fallback_display_reasons(row: Dict[str, Any]) -> List[str]:
    bucket = str(row.get("primary_bucket") or row.get("final_class") or "")
    if bucket == "REJECTED":
        return []
    quality_factors: List[str] = []
    rs = safe_float(row.get("rs_percentile") or row.get("rs_rating")) or 0.0
    flow = safe_float(row.get("flow_total_score") or row.get("flow_score") or row.get("supply_score")) or 0.0
    dist = safe_float(row.get("breakout_distance_pct") or row.get("box_distance_pct")) or 99.0
    ma = str(row.get("ma_alignment") or row.get("ma_alignment_flag") or row.get("ma_status") or "")
    liquidity = str(row.get("liquidity_status") or "")

    if rs >= 80:
        quality_factors.append("RS 리더십")
    if flow >= 25:
        quality_factors.append("수급 강함")
    if dist <= 3.0:
        quality_factors.append("박스권 상단 근접")
    if ma in {"ALIGNED", "정배열"} and liquidity == "LIQUID":
        quality_factors.append("대형주 품질")

    promotion, watch = build_promotion_reasons(bucket, quality_factors, [], [], row)
    return promotion if bucket.startswith("TIER") else watch


def compute_candidate_confidence(row: Dict[str, Any]) -> str:
    """Computes final confidence level for a candidate."""
    rs_val = float(row.get("rs_percentile", row.get("rs_rating", 0)) or 0)
    vcp_status = str(row.get("vcp_status", ""))
    vcp_conf = str(row.get("vcp_confidence", "MEDIUM"))
    vcp_cross_warn = row.get("vcp_cross_warning")
    ma_flag = str(row.get("ma_alignment_flag", ""))
    liquidity_status = str(row.get("liquidity_status", ""))
    flow_total = float(row.get("flow_total_score", 0) or 0)
    data_unit_check = str(row.get("data_unit_check", ""))
    bucket = str(row.get("primary_bucket", ""))

    if bucket == "REJECTED":
        # Cross-factor weak with high VCP raw → VERY_LOW for diagnostics
        if vcp_conf == "CROSS_FACTOR_WEAK":
            return "VERY_LOW"
        return "WEAK"

    if data_unit_check == "DATA_UNIT_WARNING" or liquidity_status == "LIQUIDITY_UNCERTAIN":
        return "DATA_REVIEW"

    conf_score = 0
    if rs_val >= 90: conf_score += 30
    elif rs_val >= 80: conf_score += 25
    elif rs_val >= 50: conf_score += 15

    if ma_flag == "ALIGNED": conf_score += 15
    elif ma_flag == "NOT_ALIGNED": conf_score -= 15

    if liquidity_status == "LIQUID": conf_score += 20
    elif liquidity_status == "ILLIQUID": conf_score -= 20

    if flow_total >= 25: conf_score += 20
    elif flow_total >= 15: conf_score += 10

    if vcp_conf == "HIGH": conf_score += 20
    elif vcp_conf == "MEDIUM": conf_score += 10
    elif vcp_conf == "LOW": conf_score -= 5
    elif vcp_conf == "CROSS_FACTOR_WEAK": conf_score -= 20

    if vcp_cross_warn: conf_score -= 20

    if conf_score >= 80: return "HIGH"
    if conf_score >= 50: return "MEDIUM"
    if conf_score >= 20: return "LOW"
    return "VERY_LOW"


def normalize_vcp_score(row: Dict[str, Any]) -> Tuple[float, float, float, str, Optional[str]]:
    """Separates VCP into raw, effective, and display scores. Cross-factor penalty.

    Returns: (raw_score, effective_score, display_score, confidence, cross_warning)
    """
    raw_score = float(row.get("vcp_raw_score") or row.get("vcp_score") or 0)
    rs_val = float(row.get("rs_percentile", row.get("rs_rating", 0)) or 0)
    ma_flag = str(row.get("ma_alignment_flag", ""))
    bucket = str(row.get("primary_bucket", ""))
    conf = str(row.get("vcp_confidence", "MEDIUM"))

    effective_score = raw_score
    cross_warnings: List[str] = []

    is_cross_weak = False
    if raw_score >= 70:
        if bucket == "REJECTED":
            cross_warnings.append("HIGH_VCP_REJECTED_BY_HARD_GATE")
            is_cross_weak = True
        if rs_val < 50:
            cross_warnings.append("LOW_RS_HIGH_VCP")
            is_cross_weak = True
        if ma_flag == "NOT_ALIGNED":
            cross_warnings.append("HIGH_VCP_BUT_MA_WEAK")
            is_cross_weak = True

    if is_cross_weak:
        effective_score = min(raw_score, 45.0)
        conf = "CROSS_FACTOR_WEAK"

    cross_warning = cross_warnings[0] if cross_warnings else None
    # 모든 cross_warning을 list로도 전달 (row에 vcp_cross_warning_list로 저장)
    row["vcp_cross_warning_list"] = cross_warnings

    return raw_score, effective_score, effective_score, conf, cross_warning


def classify_watch_alert(row: Dict[str, Any]) -> Tuple[bool, str, bool, List[str], List[str], List[str]]:
    """Determines watch alert flag, type, and action flag.

    Returns: (watch_alert_flag, watch_alert_type, action_alert_flag, reasons, exclusions, trace)
    """
    bucket = str(row.get("primary_bucket", ""))
    rs_val = float(row.get("rs_percentile", row.get("rs_rating", 0)) or 0)
    vcp_status = str(row.get("vcp_status", ""))
    breakout_status = str(row.get("breakout_status", ""))
    data_unit_check = str(row.get("data_unit_check", ""))
    liquidity_status = str(row.get("liquidity_status", ""))
    ma_flag = str(row.get("ma_alignment_flag", ""))
    flow_total = float(row.get("flow_total_score", 0) or 0)
    dist_val = float(row.get("breakout_distance_pct", 5.0) or 5.0)

    reasons: List[str] = []
    exclusions: List[str] = []
    trace: List[str] = []

    # 1. REJECTED는 알림 없음 (Invariant 1)
    if bucket == "REJECTED":
        trace.append("REJECTED 종목 알림 차단")
        return False, "NONE", False, [], ["REJECTED_BLOCK"], trace

    # 2. 잠재 사유 수집
    if bucket in {"TIER_1", "TIER_2"}:
        reasons.append(f"{bucket}_LEADERSHIP")
        trace.append(f"{bucket} 후보 선정")
    elif bucket == "TIER_3" and rs_val >= 80:
        reasons.append("TIER_3_LEADER")
        trace.append("Tier 3 + RS 80+ 선정")
    elif rs_val >= 90:
        reasons.append("HIGH_RS_CANDIDATE")
        trace.append(f"초고강도 RS({rs_val:.1f}) 관리 대상 선정")
    elif rs_val >= 80:
        if breakout_status in {"NEAR_BREAKOUT", "HIGH_CONSOLIDATION"} or dist_val <= 3.0:
            reasons.append("NEAR_BREAKOUT_LEADER")
            trace.append("RS 80+ 상단 근접 선정")
        elif ma_flag == "ALIGNED":
            reasons.append("RS_LEADERSHIP_ALIGNED")
            trace.append("RS 80+ 정배열 선정")
    elif rs_val >= 50 and bucket == "WATCHLIST" and vcp_status in VCP_CONFIRMED_STATUSES | VCP_FORMING_STATUSES:
        # Setup Watch: RS 보통+ + 베이스 형성
        reasons.append("SETUP_WATCH_CANDIDATE")
        trace.append("RS 50~80 + 베이스 형성 선정")

    if not reasons:
        # 명시적 차단 사유: RS 저조, 수급 극히 취약 (Action/Watch 모두 자격 박탈)
        if rs_val < 50:
            exclusions.append("RS 저조")
            trace.append(f"RS 저조({rs_val:.0f}) — 알림 자격 미달")
        if flow_total <= 5 and bucket not in {"TIER_1", "TIER_2"}:
            exclusions.append("수급 극히 취약")
            trace.append("수급 극히 취약 — 알림 자격 미달")
        if not exclusions:
            exclusions.append("LOW_PRIORITY")
            trace.append("주요 선정 기준 미달")
        return False, "NONE", False, [], exclusions, trace

    # 3. 제외 플래그
    if vcp_status == "REVERSE_EXPANSION":
        exclusions.append("REVERSE_EXPANSION_BLOCK")
        trace.append("역수축 변동성 확장 차단")
    if breakout_status == "FAILED_BREAKOUT":
        exclusions.append("FAILED_BREAKOUT_BLOCK")
        trace.append("최근 돌파 실패 이력 차단")
    if data_unit_check == "DATA_UNIT_WARNING":
        exclusions.append("DATA_UNIT_WARNING_ACTION_BLOCK")
        trace.append("데이터 단위 불확실 차단")
    if liquidity_status == "LIQUIDITY_UNCERTAIN":
        exclusions.append("LIQUIDITY_UNCERTAIN_ACTION_BLOCK")
        trace.append("유동성 불확실 차단")
    if rs_val < 50:
        exclusions.append("LOW_RS_BLOCK")
        trace.append("RS 50 미만 차단")
    if vcp_status == "RALLY_EXHAUSTION" or bool(row.get("vcp_rally_exhaustion_flag", False)):
        exclusions.append("RALLY_EXHAUSTION_RISK")
        trace.append("랠리 피로도 — Action 차단")
    # 수급 극히 취약은 TIER_2 리더십이 아닌 한 알림 차단
    if flow_total <= 5 and bucket not in {"TIER_1", "TIER_2"}:
        exclusions.append("수급 극히 취약")
        trace.append(f"수급 극히 취약(flow={flow_total:.0f}) — Action/Watch 차단")

    # 4. 최종 분류
    alert_flag = True
    alert_type = "NONE"
    action_flag = False

    if not exclusions:
        # Action 가능 조건
        if (
            bucket in {"TIER_1", "TIER_2", "TIER_3"} and rs_val >= 80
            and vcp_status in VCP_CONFIRMED_STATUSES | VCP_FORMING_STATUSES
            and breakout_status in {"IN_BOX", "NEAR_BREAKOUT", "BREAKOUT_CONFIRMED", "HIGH_CONSOLIDATION"}
            and liquidity_status == "LIQUID"
        ):
            alert_type = "ACTION_ALERT"
            action_flag = True
            trace.append("Priority Watch 표시(legacy ACTION_ALERT)")
        elif rs_val >= 80 and bucket in {"TIER_2", "TIER_3"}:
            alert_type = "SETUP_WATCH"
            trace.append("Setup Watch 선정 (RS 강함이나 Action 조건 미달)")
        else:
            alert_type = "SETUP_WATCH"
            trace.append("Setup Watch 선정")
    else:
        # 제외 사유가 있는 경우 → RISK_WATCH / DATA_REVIEW / NONE
        if "DATA_UNIT_WARNING_ACTION_BLOCK" in exclusions or "LIQUIDITY_UNCERTAIN_ACTION_BLOCK" in exclusions:
            if rs_val >= 90:
                alert_type = "DATA_REVIEW"
                reasons.append("DATA_REVIEW_REQUIRED")
                trace.append("Action 차단되나 초고RS로 Data Review 전환")
            elif rs_val >= 80:
                alert_type = "DATA_REVIEW"
                reasons.append("DATA_REVIEW_REQUIRED")
                trace.append("데이터 검증 필요 (RS 강함)")
            elif rs_val >= 50:
                alert_type = "DATA_REVIEW"
                reasons.append("DATA_REVIEW_REQUIRED")
                trace.append("데이터 검증 필요")
            else:
                alert_flag = False
                alert_type = "NONE"
                trace.append("RS 약함 + 데이터 결함 → 알림 제외")
        elif "RALLY_EXHAUSTION_RISK" in exclusions and rs_val >= 80:
            alert_type = "RISK_WATCH"
            reasons.append("RALLY_EXHAUSTION_RISK")
            trace.append("랠리 피로도 — Risk Watch 전환")
        elif rs_val >= 90:
            alert_type = "RISK_WATCH"
            reasons.append("HIGH_RS_RISK_WATCH")
            trace.append("Action 차단되나 초고RS로 Risk Watch 전환")
        elif rs_val >= 80 and (
            "REVERSE_EXPANSION_BLOCK" in exclusions or "FAILED_BREAKOUT_BLOCK" in exclusions
        ) and "수급 극히 취약" not in exclusions:
            alert_type = "RISK_WATCH"
            reasons.append("RISK_WATCH_REQUIRED")
            trace.append("리스크 요인으로 Risk Watch 전환")
        elif "수급 극히 취약" in exclusions:
            alert_flag = False
            alert_type = "NONE"
            trace.append("수급 극히 취약 — 알림 자격 박탈")
        else:
            alert_flag = False
            alert_type = "NONE"
            trace.append("치명적 결함으로 알림 제외")

    return alert_flag, alert_type, action_flag, reasons, exclusions, trace


def check_policy_invariants(row: Dict[str, Any]) -> List[str]:
    """Checks for any policy violations. Returns list of error messages."""
    errors: List[str] = []
    bucket = str(row.get("primary_bucket", ""))
    vcp_status = str(row.get("vcp_status", ""))
    breakout_status = str(row.get("breakout_status", ""))
    data_unit = str(row.get("data_unit_check", ""))
    liq_status = str(row.get("liquidity_status", ""))
    alert_flag = bool(row.get("watch_alert_flag", False))
    alert_type = str(row.get("watch_alert_type", "NONE"))
    action_flag = bool(row.get("action_alert_flag", False))
    rs_val = float(row.get("rs_percentile", row.get("rs_rating", 0)) or 0)
    vcp_raw = float(row.get("vcp_raw_score", 0) or 0)
    vcp_eff = float(row.get("vcp_effective_score", 0) or 0)

    # 1. REJECTED는 알림 켜질 수 없음
    if bucket == "REJECTED" and alert_flag:
        errors.append("Invariant 1: REJECTED 종목에 알림이 켜짐")

    # 2-5. 특정 결함 상태에서는 ACTION 금지
    if vcp_status == "REVERSE_EXPANSION" and action_flag:
        errors.append("Invariant 2: REVERSE_EXPANSION 종목에 Action Alert 발동")
    if breakout_status == "FAILED_BREAKOUT" and action_flag:
        errors.append("Invariant 3: FAILED_BREAKOUT 종목에 Action Alert 발동")
    if data_unit == "DATA_UNIT_WARNING" and action_flag:
        errors.append("Invariant 4: DATA_UNIT_WARNING 종목에 Action Alert 발동")
    if liq_status == "LIQUIDITY_UNCERTAIN" and action_flag:
        errors.append("Invariant 5: LIQUIDITY_UNCERTAIN 종목에 Action Alert 발동")

    # 6-8. Alert flag/type 정합성
    if alert_flag and alert_type == "NONE":
        errors.append("Invariant 6: 알림은 켜졌으나 유형이 NONE임")
    if action_flag and alert_type != "ACTION_ALERT":
        errors.append("Invariant 7: action_flag=True이나 type이 ACTION_ALERT가 아님")
    if alert_type == "ACTION_ALERT" and not action_flag:
        errors.append("Invariant 8: type이 ACTION_ALERT이나 action_flag가 False")

    # 9. 알림 켜졌는데 사유 없음
    if alert_flag and not as_reason_list(row.get("watch_alert_reasons")):
        errors.append("Invariant 9: 알림은 켜졌으나 사유(reasons)가 없음")

    # 10. 초고RS + 알림 없음 + 추적 없음
    if not alert_flag and rs_val >= 90 and not (
        as_reason_list(row.get("watch_alert_exclusion_reasons")) or row.get("watch_alert_decision_trace")
    ):
        errors.append("Invariant 10: 초고RS인데 제외 사유나 추적 기록 없음")

    # VCP cross-factor invariants
    if vcp_raw >= 70 and rs_val < 50 and not (row.get("vcp_cross_warning") or row.get("vcp_cross_warning_list")):
        errors.append("Policy: Low RS + High VCP인데 cross_warning이 없음")
    if vcp_raw >= 70 and bucket == "REJECTED" and vcp_eff > 45:
        errors.append("Policy: Rejected + High VCP인데 effective_score가 45 초과")

    return errors


def validate_policy_invariants(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Batch-validate a list of result rows. Returns aggregated diagnostics.

    Returns:
        {
            "policy_violation_count": int,
            "policy_violation_records": [{"symbol":..., "violations": [...]}],
            "missing_required_field_count": int,
            "missing_required_field_records": [...],
            "score_max_inconsistent_count": int,
            "score_max_inconsistent_records": [...],
            "empty_display_reason_records": [...],
            "watch_alert_type_distribution": {...},
            "candidate_confidence_distribution": {...},
            "vcp_cross_warning_distribution": {...},
        }
    """
    required_fields = (
        "symbol", "name", "primary_bucket", "total_score", "score_max",
        "rs_percentile", "vcp_status", "liquidity_status",
        "watch_alert_flag", "watch_alert_type",
    )

    violations_records: List[Dict[str, Any]] = []
    missing_records: List[Dict[str, Any]] = []
    score_max_inconsistent: List[Dict[str, Any]] = []
    empty_display_reason: List[Dict[str, Any]] = []
    missing_display_promotion: List[Dict[str, Any]] = []
    missing_display_rejected: List[Dict[str, Any]] = []
    watch_alert_type_missing: List[Dict[str, Any]] = []
    untranslated_reason_records: List[Dict[str, Any]] = []
    duplicate_cleanup_records: List[Dict[str, Any]] = []
    vcp_raw_missing: List[Dict[str, Any]] = []

    alert_type_counter: Dict[str, int] = {}
    confidence_counter: Dict[str, int] = {}
    cross_warning_counter: Dict[str, int] = {}

    seen_score_max = set()
    for row in rows:
        symbol = str(row.get("symbol", row.get("code", "?")))
        bucket = str(row.get("primary_bucket", ""))
        display_row = normalize_result_schema(dict(row))

        # Invariant violations
        v = check_policy_invariants(row)
        if v:
            violations_records.append({
                "symbol": symbol,
                "name": str(row.get("name", "")),
                "primary_bucket": bucket,
                "violations": v,
            })

        # Missing required fields
        missing = [f for f in required_fields if f not in row or row.get(f) is None]
        if missing:
            missing_records.append({
                "symbol": symbol,
                "name": str(row.get("name", "")),
                "missing_fields": missing,
            })

        # score_max consistency
        sm = row.get("score_max")
        if sm is not None:
            try:
                seen_score_max.add(float(sm))
            except (TypeError, ValueError):
                pass

        # Empty display reasons for non-REJECTED
        if bucket in {"TIER_1", "TIER_2", "TIER_3", "WATCHLIST"}:
            disp = as_reason_list(display_row.get("display_promotion_reasons"))
            promo = as_reason_list(display_row.get("promotion_reasons"))
            watch = as_reason_list(display_row.get("watchlist_reasons"))
            if not disp and not promo and not watch:
                empty_display_reason.append({
                    "symbol": symbol,
                    "name": str(row.get("name", "")),
                    "primary_bucket": bucket,
                })
            if not disp and not promo and not watch:
                missing_display_promotion.append({
                    "symbol": symbol,
                    "name": str(row.get("name", "")),
                    "primary_bucket": bucket,
                })
        if bucket == "REJECTED":
            rejected_display = get_display_rejected_reasons(display_row)
            if not rejected_display:
                missing_display_rejected.append({
                    "symbol": symbol,
                    "name": str(row.get("name", "")),
                    "primary_bucket": bucket,
                })

        if bool(row.get("watch_alert_flag", False)) and str(row.get("watch_alert_type", "NONE")) in {"", "NONE", "nan", "None"}:
            watch_alert_type_missing.append({
                "symbol": symbol,
                "name": str(row.get("name", "")),
                "primary_bucket": bucket,
            })

        raw_reason_values: List[str] = []
        for field in (
            "display_promotion_reasons", "display_rejected_reasons", "display_watch_alert_reasons",
            "display_restriction_reasons", "watch_alert_reasons_display", "watch_alert_exclusion_reasons_display",
        ):
            raw_reason_values.extend(_expand_reason_items(display_row.get(field)))
        untranslated = [
            r for r in raw_reason_values
            if str(r).strip() in REASON_LABEL_MAP
        ]
        if untranslated:
            untranslated_reason_records.append({
                "symbol": symbol,
                "name": str(row.get("name", "")),
                "reasons": dedupe_keep_order(untranslated),
            })

        for field in ("display_promotion_reasons", "display_rejected_reasons", "display_watch_alert_reasons", "display_restriction_reasons"):
            original = as_reason_list(display_row.get(field))
            cleaned = clean_display_reasons(original, bucket)
            if len(original) != len(cleaned):
                duplicate_cleanup_records.append({
                    "symbol": symbol,
                    "name": str(row.get("name", "")),
                    "field": field,
                    "before": original,
                    "after": cleaned,
                })
                break

        if row.get("vcp_raw_score") is None:
            vcp_raw_missing.append({
                "symbol": symbol,
                "name": str(row.get("name", "")),
                "primary_bucket": bucket,
            })

        # Distributions
        at = str(row.get("watch_alert_type", "NONE"))
        alert_type_counter[at] = alert_type_counter.get(at, 0) + 1
        cc = str(row.get("candidate_confidence", "WEAK"))
        confidence_counter[cc] = confidence_counter.get(cc, 0) + 1
        cw = row.get("vcp_cross_warning") or "NONE"
        cw_key = str(cw) if cw else "NONE"
        cross_warning_counter[cw_key] = cross_warning_counter.get(cw_key, 0) + 1

    if len(seen_score_max) > 1:
        score_max_inconsistent.append({
            "distinct_values": sorted(seen_score_max),
            "message": "동일 run에서 score_max가 일관되지 않음 — 표시/분모 오류 가능",
        })

    return {
        "policy_violation_count": len(violations_records),
        "policy_violation_records": violations_records[:50],
        "missing_required_field_count": len(missing_records),
        "missing_required_field_records": missing_records[:50],
        "score_max_inconsistent_count": len(score_max_inconsistent),
        "score_max_inconsistent_records": score_max_inconsistent,
        "empty_display_reason_count": len(empty_display_reason),
        "empty_display_reason_records": empty_display_reason[:50],
        "missing_display_promotion_reason_count": len(missing_display_promotion),
        "missing_display_promotion_reason_records": missing_display_promotion[:50],
        "missing_display_rejected_reason_count": len(missing_display_rejected),
        "missing_display_rejected_reason_records": missing_display_rejected[:50],
        "watch_alert_type_missing_count": len(watch_alert_type_missing),
        "watch_alert_type_missing_records": watch_alert_type_missing[:50],
        "reason_code_untranslated_count": len(untranslated_reason_records),
        "reason_code_untranslated_records": untranslated_reason_records[:50],
        "duplicate_reason_cleanup_count": len(duplicate_cleanup_records),
        "duplicate_reason_cleanup_records": duplicate_cleanup_records[:50],
        "vcp_raw_missing_count": len(vcp_raw_missing),
        "vcp_raw_missing_records": vcp_raw_missing[:50],
        "watch_alert_type_distribution": alert_type_counter,
        "candidate_confidence_distribution": confidence_counter,
        "vcp_cross_warning_distribution": cross_warning_counter,
    }


POLICY_METADATA = {
    "name": "AlphaForge Policy Engine",
    "version": "v6.2",
    "description": "Rule-based classification policy for Tier, display labels, VCP confidence, and candidate confidence.",
    "score_max_default": DEFAULT_SCORE_MAX,
    "features": [
        "primary_bucket_classification",
        "watch_alert_type",
        "action_alert_guard",
        "risk_watch",
        "data_review",
        "vcp_raw_effective_display_score",
        "candidate_confidence",
        "policy_invariant_validation",
        "schema_alias_preservation",
        "score_max_run_level_constant",
    ],
    "watch_alert_types": [
        "ACTION_ALERT",
        "RISK_WATCH",
        "DATA_REVIEW",
        "SETUP_WATCH",
        "NONE",
    ],
    "display_labels": sorted(DISPLAY_LABELS),
    "screening_modes": ["EXPLORE_MODE", "STRICT_MODE", "HYBRID_MODE"],
    "invariants": [
        "REJECTED cannot have watch_alert_flag=True",
        "REVERSE_EXPANSION cannot be ACTION_ALERT",
        "FAILED_BREAKOUT cannot be ACTION_ALERT",
        "DATA_UNIT_WARNING cannot be ACTION_ALERT",
        "LIQUIDITY_UNCERTAIN cannot be ACTION_ALERT",
        "score_max is a run-level constant, not row-inferred",
    ],
}


def policy_metadata() -> dict:
    """Return JSON-safe policy metadata for API responses."""
    return dict(POLICY_METADATA)


def validate_ai_provider(provider: str) -> str:
    """Validates and normalizes the AI provider name."""
    if not provider:
        return "gemini"
    p = str(provider).lower().strip()
    if p in {"gemini", "openai", "anthropic"}:
        return p
    return "gemini"  # Default
