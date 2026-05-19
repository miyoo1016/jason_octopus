from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


logger = logging.getLogger(__name__)

DEFAULT_CANDIDATE_EXPORT_PATH = Path("data/exports/alphaforge_candidates.json")
DEFAULT_DUAL_HORIZON_EXPORT_PATH = Path("data/exports/alphaforge_dual_horizon.json")
DEFAULT_DAILY_HISTORY_PATH = Path("data/history/alphaforge_daily_signals.jsonl")
DAILY_HISTORY_TIERS = {"TIER_2", "TIER_3", "WATCHLIST", "REJECTED"}
CANDIDATE_EXPORT_LIMIT = 5
DUAL_HORIZON_FIELDS = (
    "short_swing_score",
    "position_swing_score",
    "horizon_label",
    "short_reasons",
    "position_reasons",
)
VCP_CONFIRMED_STATUSES = {"VCP_STRICT", "VCP_VALID", "VCP_CONFIRMED"}
VCP_FORMING_STATUSES = {"VCP_WARNING", "BASE_BUILDING", "HIGH_CONSOLIDATION", "NEAR_SETUP", "VCP_FORMING", "CONTRACTION_WARN"}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict, set)):
        return False
    if hasattr(value, "shape") and getattr(value, "shape", ()) != ():
        return False
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        return False
    return isinstance(value, float) and math.isnan(value)


def _clean_value(value: Any) -> Any:
    if _is_missing(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def _first_present(row: pd.Series, *keys: str) -> Any:
    for key in keys:
        if key in row.index:
            value = row.get(key)
            if not _is_missing(value):
                return _clean_value(value)
    return None


def _as_list(value: Any) -> list[Any]:
    value = _clean_value(value)
    if value is None:
        return []
    if isinstance(value, list):
        return [_clean_value(v) for v in value if not _is_missing(v)]
    if isinstance(value, tuple):
        return [_clean_value(v) for v in value if not _is_missing(v)]
    if hasattr(value, "tolist"):
        try:
            converted = value.tolist()
            return _as_list(converted)
        except (TypeError, ValueError):
            pass
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _combined_list(row: pd.Series, *keys: str) -> list[Any]:
    combined: list[Any] = []
    seen: set[str] = set()
    for key in keys:
        if key not in row.index:
            continue
        for item in _as_list(row.get(key)):
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if marker in seen:
                continue
            seen.add(marker)
            combined.append(item)
    return combined


def _safe_float(value: Any, default: float | None = None) -> float | None:
    value = _clean_value(value)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct_value(value: Any) -> float | None:
    pct = _safe_float(value)
    if pct is None:
        return None
    return pct * 100.0 if abs(pct) <= 1.0 else pct


def _score_short_horizon(row: pd.Series) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    change_pct = _pct_value(_first_present(row, "change_pct", "price_change_pct", "change_rate"))
    trading_value = _safe_float(_first_present(row, "trading_value", "liquidity_trading_value", "raw_trading_value"))
    volume_ratio = _safe_float(_first_present(row, "breakout_volume_ratio", "volume_ratio"))
    breakout_status = str(_first_present(row, "breakout_status", "box_breakout_flag") or "")
    breakout_pct = _safe_float(_first_present(row, "box_breakout_pct", "breakout_pct"))
    distance_pct = _safe_float(_first_present(row, "breakout_distance_pct", "box_distance_pct"))
    vcp_status = str(_first_present(row, "vcp_status") or "")
    alert_type = str(_first_present(row, "watch_alert_type", "display_watch_alert_type") or "")

    if change_pct is not None:
        if 0.5 <= change_pct <= 8.0:
            score += 18
            reasons.append(f"등락률 양호 {change_pct:.1f}%")
        elif change_pct > 8.0:
            score += 8
            reasons.append(f"단기 급등 {change_pct:.1f}%")
        elif change_pct >= -1.5:
            score += 8
            reasons.append(f"가격 안정 {change_pct:.1f}%")
        else:
            score -= 8
            reasons.append(f"단기 약세 {change_pct:.1f}%")

    if trading_value is not None:
        if trading_value >= 50_000_000_000:
            score += 16
            reasons.append("거래대금 강함")
        elif trading_value >= 10_000_000_000:
            score += 10
            reasons.append("거래대금 양호")
    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            score += 12
            reasons.append(f"거래량 확장 {volume_ratio:.1f}배")
        elif volume_ratio >= 1.0:
            score += 6
            reasons.append(f"거래량 확인 {volume_ratio:.1f}배")

    if breakout_status == "BREAKOUT_CONFIRMED":
        score += 22
        reasons.append("박스 돌파 확인")
    elif breakout_status == "NEAR_BREAKOUT":
        score += 18
        reasons.append("박스 상단 근접")
    elif breakout_status == "HIGH_CONSOLIDATION":
        score += 12
        reasons.append("고가권 압축")
    elif breakout_status == "IN_BOX":
        score += 8
        reasons.append("박스권 내부")
    elif breakout_status == "FAILED_BREAKOUT":
        score -= 20
        reasons.append("돌파 실패 위험")

    if distance_pct is not None and distance_pct <= 3.0:
        score += 10
        reasons.append(f"상단 거리 {distance_pct:.1f}%")
    elif breakout_pct is not None and breakout_pct >= 0:
        score += 8
        reasons.append(f"상단 돌파 {breakout_pct:.1f}%")

    if alert_type == "ACTION_ALERT":
        score += 16
        reasons.append("PRIORITY_WATCH")
    elif alert_type in {"SETUP_WATCH", "RISK_WATCH"}:
        score += 6
        reasons.append(alert_type)

    if vcp_status in {"RALLY_EXHAUSTION", "REVERSE_EXPANSION"}:
        score -= 12
        reasons.append(f"VCP 위험 {vcp_status}")
    if breakout_status == "FAILED_BREAKOUT":
        score -= 4
    return max(0, min(100, int(round(score)))), reasons[:6]


def _score_position_horizon(row: pd.Series) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    rs = _safe_float(_first_present(row, "rs_percentile", "rs_rating", "rs_score"))
    vcp_status = str(_first_present(row, "vcp_status") or "")
    vcp_rally_flag = bool(_first_present(row, "vcp_rally_exhaustion_flag"))
    ma_flag = str(_first_present(row, "ma_alignment_flag") or "")
    flow_score = _safe_float(_first_present(row, "flow_total_score", "stock_flow_score", "final_flow_bias", "flow_score"))
    tier = str(_first_present(row, "primary_bucket", "candidate_status") or "")
    market_cap = _safe_float(_first_present(row, "market_cap"))
    liquidity_status = str(_first_present(row, "liquidity_status") or "")

    if rs is not None:
        if rs >= 90:
            score += 24
            reasons.append(f"RS 상위 {rs:.1f}")
        elif rs >= 80:
            score += 18
            reasons.append(f"RS 강함 {rs:.1f}")
        elif rs >= 60:
            score += 10
            reasons.append(f"RS 보통 {rs:.1f}")
        elif rs < 50:
            score -= 12
            reasons.append(f"RS 약함 {rs:.1f}")

    if vcp_status in VCP_CONFIRMED_STATUSES:
        score += 22
        reasons.append(vcp_status)
    elif vcp_status in VCP_FORMING_STATUSES:
        score += 12
        reasons.append(f"구조 관찰 {vcp_status}")
    elif vcp_status in {"RALLY_EXHAUSTION", "REVERSE_EXPANSION"} or vcp_rally_flag:
        score -= 12
        reasons.append(f"VCP 위험 {vcp_status}")

    if ma_flag == "ALIGNED":
        score += 16
        reasons.append("정배열")
    elif ma_flag == "NOT_ALIGNED":
        score -= 6
        reasons.append("정배열 아님")

    if flow_score is not None:
        if flow_score >= 25:
            score += 16
            reasons.append("수급 강함")
        elif flow_score >= 15:
            score += 10
            reasons.append("수급 양호")
        elif flow_score <= 5:
            score -= 8
            reasons.append("수급 약함")

    if tier == "TIER_2":
        score += 16
        reasons.append("Tier 2")
    elif tier == "TIER_3":
        score += 10
        reasons.append("Tier 3")
    elif tier == "WATCHLIST":
        score += 4
        reasons.append("Watchlist")
    elif tier == "REJECTED":
        score -= 18
        reasons.append("Rejected")

    if market_cap is not None and market_cap >= 5_000_000_000_000:
        score += 6
        reasons.append("대형주 품질")
    if liquidity_status == "LIQUID":
        score += 6
        reasons.append("유동성 양호")
    return max(0, min(100, int(round(score)))), reasons[:6]


def _has_box_attempt(row: pd.Series) -> bool:
    breakout_status = str(_first_present(row, "breakout_status", "box_breakout_flag") or "")
    breakout_pct = _safe_float(_first_present(row, "box_breakout_pct", "breakout_pct"))
    distance_pct = _safe_float(_first_present(row, "breakout_distance_pct", "box_distance_pct"))
    return (
        breakout_status in {"BREAKOUT_CONFIRMED", "NEAR_BREAKOUT", "HIGH_CONSOLIDATION", "FAILED_BREAKOUT"}
        or (distance_pct is not None and distance_pct <= 7.0)
        or (breakout_pct is not None and breakout_pct >= -7.0)
    )


def _has_short_quality(row: pd.Series) -> bool:
    trading_value = _safe_float(_first_present(row, "trading_value", "liquidity_trading_value", "raw_trading_value"))
    market_cap = _safe_float(_first_present(row, "market_cap"))
    liquidity_status = str(_first_present(row, "liquidity_status") or "")
    return (
        (trading_value is not None and trading_value >= 10_000_000_000)
        or (market_cap is not None and market_cap >= 5_000_000_000_000)
        or liquidity_status == "LIQUID"
    )


def _has_short_momentum(row: pd.Series, short_score: int) -> bool:
    rs = _safe_float(_first_present(row, "rs_percentile", "rs_rating", "rs_score"))
    alert_type = str(_first_present(row, "watch_alert_type", "display_watch_alert_type") or "")
    change_pct = _pct_value(_first_present(row, "change_pct", "price_change_pct", "change_rate"))
    return (
        short_score >= 45
        or (rs is not None and rs >= 80 and _has_box_attempt(row))
        or alert_type == "ACTION_ALERT"
        or (change_pct is not None and change_pct >= 0.5 and _has_box_attempt(row))
    )


def _horizon_label(short_score: int, position_score: int, row: pd.Series) -> str:
    vcp_status = str(_first_present(row, "vcp_status") or "")
    vcp_rally_flag = bool(_first_present(row, "vcp_rally_exhaustion_flag"))
    breakout_status = str(_first_present(row, "breakout_status") or "")
    tier = str(_first_present(row, "primary_bucket", "candidate_status") or "")
    has_risk_flag = vcp_status in {"RALLY_EXHAUSTION", "REVERSE_EXPANSION"} or vcp_rally_flag or breakout_status == "FAILED_BREAKOUT"
    box_attempt = _has_box_attempt(row)
    short_quality = _has_short_quality(row)
    short_momentum = _has_short_momentum(row, short_score)
    chase_risk = has_risk_flag and short_momentum and box_attempt and short_quality

    if chase_risk:
        return "CHASE_RISK"
    if short_score >= 60 and position_score >= 60:
        return "OVERLAP"
    if short_score >= 60 and not has_risk_flag:
        return "SHORT_SWING"
    if short_score >= 45 and box_attempt and short_quality and not has_risk_flag:
        return "SHORT_WATCH"
    if position_score >= 60:
        return "POSITION_SWING"
    if short_momentum and box_attempt and short_quality:
        return "SHORT_WATCH"
    if has_risk_flag or tier == "REJECTED":
        return "RISK_ONLY"
    return "WATCH_ONLY"


def add_dual_horizon_fields(final_df: pd.DataFrame) -> pd.DataFrame:
    if final_df is None or final_df.empty:
        return final_df
    out = final_df.copy()
    short_scores: list[int] = []
    position_scores: list[int] = []
    labels: list[str] = []
    short_reasons: list[list[str]] = []
    position_reasons: list[list[str]] = []
    for _, row in out.iterrows():
        short_score, s_reasons = _score_short_horizon(row)
        position_score, p_reasons = _score_position_horizon(row)
        short_scores.append(short_score)
        position_scores.append(position_score)
        labels.append(_horizon_label(short_score, position_score, row))
        short_reasons.append(s_reasons)
        position_reasons.append(p_reasons)
    out["short_swing_score"] = short_scores
    out["position_swing_score"] = position_scores
    out["horizon_label"] = labels
    out["short_reasons"] = short_reasons
    out["position_reasons"] = position_reasons
    return out


def _candidate_record(row: pd.Series, generated_at: str) -> dict[str, Any]:
    primary_bucket = _first_present(row, "primary_bucket", "candidate_status", "tier")
    legacy_label = _first_present(row, "legacy_label", "watch_alert_type", "alert_type")
    display_label = _first_present(row, "final_label", "display_label", "display_watch_alert_type")
    return {
        "symbol": _first_present(row, "symbol", "code"),
        "name": _first_present(row, "name"),
        "tier": primary_bucket,
        "alert_type": _first_present(row, "watch_alert_type", "display_watch_alert_type"),
        "legacy_label": legacy_label,
        "legacyLabel": legacy_label,
        "display_label": display_label,
        "displayLabel": display_label,
        "final_label": display_label,
        "finalLabel": display_label,
        "buy_gate_passed": _first_present(row, "buy_gate_passed", "buyGatePassed"),
        "failed_buy_gates": _combined_list(row, "failed_buy_gates", "failedBuyGates"),
        "buy_gate_reason": _first_present(row, "buy_gate_reason", "buyGateReason"),
        "rs": _first_present(row, "rs_percentile", "rs_rating", "rs_score"),
        "vcp_status": _first_present(row, "vcp_status"),
        "vcp_component_scores": _first_present(row, "vcp_component_scores", "vcpComponentScores"),
        "vcpComponentScores": _first_present(row, "vcpComponentScores", "vcp_component_scores"),
        "vcp_quality_reason": _first_present(row, "vcp_quality_reason", "vcpQualityReason"),
        "vcpQualityReason": _first_present(row, "vcpQualityReason", "vcp_quality_reason"),
        "box_upper_price": _first_present(
            row,
            "box_upper_price",
            "box_high",
            "pivot_price",
            "breakout_level",
            "resistance_price",
            "box_top",
            "recent_high",
        ),
        "total_score": _first_present(row, "total_score"),
        "generated_at": generated_at,
        "recommendation_action": _first_present(row, "recommendation_action"),
        "recommendation_score": _first_present(row, "recommendation_score"),
        "recommendation_rank": _first_present(row, "recommendation_rank"),
        "recommendation_reason": _first_present(row, "recommendation_reason"),
        "entry_trigger": _first_present(row, "entry_trigger"),
        "invalidation_condition": _first_present(row, "invalidation_condition"),
        "suggested_position_size": _first_present(row, "suggested_position_size"),
    }


def _sort_value_series(df: pd.DataFrame, *keys: str) -> pd.Series:
    for key in keys:
        if key in df.columns:
            return pd.to_numeric(df[key], errors="coerce").fillna(0)
    return pd.Series([0] * len(df), index=df.index)


def _rank_candidate_rows(df: pd.DataFrame) -> pd.DataFrame:
    ranked = df.copy()
    ranked["_candidate_total_score"] = _sort_value_series(ranked, "total_score")
    ranked["_candidate_short_score"] = _sort_value_series(ranked, "short_swing_score")
    ranked["_candidate_position_score"] = _sort_value_series(ranked, "position_swing_score")
    ranked["_candidate_rs"] = _sort_value_series(ranked, "rs_percentile", "rs_rating", "rs_score")
    ranked["_candidate_trading_value"] = _sort_value_series(
        ranked,
        "trading_value",
        "liquidity_trading_value",
        "raw_trading_value",
    )
    return ranked.sort_values(
        [
            "_candidate_total_score",
            "_candidate_short_score",
            "_candidate_position_score",
            "_candidate_rs",
            "_candidate_trading_value",
        ],
        ascending=[False, False, False, False, False],
        kind="mergesort",
    ).drop(
        columns=[
            "_candidate_total_score",
            "_candidate_short_score",
            "_candidate_position_score",
            "_candidate_rs",
            "_candidate_trading_value",
        ],
        errors="ignore",
    )


def _candidate_identity(row: pd.Series) -> str:
    return str(_first_present(row, "symbol", "code") or "")


def _select_candidate_rows(final_df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if final_df is None or final_df.empty:
        return pd.DataFrame(), 0

    enriched = add_dual_horizon_fields(final_df)
    primary_bucket = (
        enriched["primary_bucket"].astype(str)
        if "primary_bucket" in enriched.columns
        else pd.Series([""] * len(enriched), index=enriched.index)
    )
    watch_alert_type = (
        enriched["watch_alert_type"].astype(str)
        if "watch_alert_type" in enriched.columns
        else pd.Series([""] * len(enriched), index=enriched.index)
    )
    non_rejected = primary_bucket != "REJECTED"
    primary_mask = non_rejected & (
        primary_bucket.isin(["TIER_2", "TIER_3"])
        | (watch_alert_type == "ACTION_ALERT")
    )
    selected = _rank_candidate_rows(enriched[primary_mask]).head(CANDIDATE_EXPORT_LIMIT)
    selected_symbols = {
        _candidate_identity(row)
        for _, row in selected.iterrows()
        if _candidate_identity(row)
    }

    fallback_fill_count = 0
    if len(selected) < CANDIDATE_EXPORT_LIMIT:
        fallback_mask = non_rejected & (
            (primary_bucket == "WATCHLIST")
            | watch_alert_type.isin(["RISK_WATCH", "SETUP_WATCH"])
        )
        fallback_pool = enriched[fallback_mask].copy()
        if selected_symbols:
            fallback_pool = fallback_pool[
                ~fallback_pool.apply(lambda row: _candidate_identity(row) in selected_symbols, axis=1)
            ]
        need = CANDIDATE_EXPORT_LIMIT - len(selected)
        fallback = _rank_candidate_rows(fallback_pool).head(need)
        fallback_fill_count = len(fallback)
        if fallback_fill_count:
            selected = pd.concat([selected, fallback], ignore_index=True)

    return selected.head(CANDIDATE_EXPORT_LIMIT), fallback_fill_count


def export_alphaforge_candidates(
    final_df: pd.DataFrame,
    export_path: str | os.PathLike[str] = DEFAULT_CANDIDATE_EXPORT_PATH,
    generated_at: str | None = None,
) -> int:
    """Persist ACTION_ALERT or TIER_3 candidates without changing scoring output."""
    generated_at = generated_at or datetime.now().isoformat(timespec="seconds")
    path = Path(export_path)

    candidates, fallback_fill_count = _select_candidate_rows(final_df)
    records = [_candidate_record(row, generated_at) for _, row in candidates.iterrows()]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    logger.info(
        "AlphaForge candidates export: count=%s fallback_fill_count=%s",
        len(records),
        fallback_fill_count,
    )
    if fallback_fill_count:
        print(f"[AlphaForge candidates] fallback_fill_count={fallback_fill_count}")
    return len(records)


def _json_ready_record(row: pd.Series) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in row.to_dict().items():
        if key in {"short_reasons", "position_reasons", "risk_flags"}:
            record[key] = _as_list(value)
        else:
            record[key] = _clean_value(value)
    return record


def export_alphaforge_dual_horizon(
    final_df: pd.DataFrame,
    export_path: str | os.PathLike[str] = DEFAULT_DUAL_HORIZON_EXPORT_PATH,
) -> int:
    """Persist all final rows with dual horizon fields for downstream checks."""
    path = Path(export_path)
    enriched = add_dual_horizon_fields(final_df)
    records = [] if enriched is None or enriched.empty else [_json_ready_record(row) for _, row in enriched.iterrows()]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, default=str)
    return len(records)


def format_dual_horizon_console(final_df: pd.DataFrame, limit: int | None = None) -> str:
    enriched = add_dual_horizon_fields(final_df)
    if enriched is None or enriched.empty:
        return "[Dual Horizon]\n  결과 없음"
    rows = enriched.head(limit) if limit else enriched
    lines = ["[Dual Horizon]"]
    for _, row in rows.iterrows():
        symbol = _first_present(row, "symbol", "code") or "-"
        name = _first_present(row, "name") or "-"
        short_score = int(_safe_float(row.get("short_swing_score"), 0) or 0)
        position_score = int(_safe_float(row.get("position_swing_score"), 0) or 0)
        short_reason = ", ".join(str(x) for x in _as_list(row.get("short_reasons"))[:2]) or "-"
        position_reason = ", ".join(str(x) for x in _as_list(row.get("position_reasons"))[:2]) or "-"
        label = str(row.get("horizon_label") or "-")
        lines.append(
            f"  {symbol} {name} | 단기: {short_score} ({short_reason}) | "
            f"중기: {position_score} ({position_reason}) | {label}"
        )
    return "\n".join(lines)


def _history_record(
    row: pd.Series,
    *,
    run_date: str,
    generated_at: str,
    market: str | None,
    universe_count: int | None,
) -> dict[str, Any]:
    return {
        "run_date": run_date,
        "generated_at": generated_at,
        "market": market,
        "universe_count": universe_count,
        "symbol": _first_present(row, "symbol", "code"),
        "name": _first_present(row, "name"),
        "tier": _first_present(row, "primary_bucket", "candidate_status", "tier"),
        "alert_type": _first_present(row, "watch_alert_type", "display_watch_alert_type"),
        "legacy_label": _first_present(row, "legacy_label", "watch_alert_type", "alert_type"),
        "display_label": _first_present(row, "final_label", "display_label", "display_watch_alert_type"),
        "final_label": _first_present(row, "final_label", "display_label", "display_watch_alert_type"),
        "buy_gate_passed": _first_present(row, "buy_gate_passed", "buyGatePassed"),
        "failed_buy_gates": _combined_list(row, "failed_buy_gates", "failedBuyGates"),
        "buy_gate_reason": _first_present(row, "buy_gate_reason", "buyGateReason"),
        "rs": _first_present(row, "rs_percentile", "rs_rating", "rs_score"),
        "vcp_status": _first_present(row, "vcp_status"),
        "vcp_component_scores": _first_present(row, "vcp_component_scores", "vcpComponentScores"),
        "vcpComponentScores": _first_present(row, "vcpComponentScores", "vcp_component_scores"),
        "vcp_quality_reason": _first_present(row, "vcp_quality_reason", "vcpQualityReason"),
        "vcpQualityReason": _first_present(row, "vcpQualityReason", "vcp_quality_reason"),
        "box_upper_price": _first_present(
            row,
            "box_upper_price",
            "box_high",
            "pivot_price",
            "breakout_level",
            "resistance_price",
            "box_top",
            "recent_high",
        ),
        "total_score": _first_present(row, "total_score"),
        "close_price": _first_present(row, "close_price", "close", "liquidity_close", "liquidity_price"),
        "reasons": _combined_list(
            row,
            "display_promotion_reasons",
            "display_watch_alert_reasons",
            "watch_alert_reasons",
            "promotion_reasons",
            "watchlist_reasons",
            "display_rejected_reasons",
            "rejected_reasons",
        ),
        "risk_flags": _combined_list(row, "risk_flags"),
    }


def _history_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("run_date") or ""),
        str(record.get("symbol") or ""),
        str(record.get("tier") or ""),
    )


def export_alphaforge_daily_history(
    final_df: pd.DataFrame,
    *,
    run_date: str,
    market: str | None,
    universe_count: int | None,
    history_path: str | os.PathLike[str] = DEFAULT_DAILY_HISTORY_PATH,
    generated_at: str | None = None,
) -> int:
    """Append daily Tier/Watch/Rejected rows, skipping duplicate run_date+symbol+tier keys."""
    generated_at = generated_at or datetime.now().isoformat(timespec="seconds")
    path = Path(history_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_keys: set[tuple[str, str, str]] = set()
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing_keys.add(_history_key(json.loads(line)))
                except json.JSONDecodeError:
                    continue

    if final_df is None or final_df.empty or "primary_bucket" not in final_df.columns:
        new_records: list[dict[str, Any]] = []
    else:
        tiers = final_df["primary_bucket"].astype(str)
        history_rows = final_df[tiers.isin(DAILY_HISTORY_TIERS)]
        new_records = []
        for _, row in history_rows.iterrows():
            record = _history_record(
                row,
                run_date=run_date,
                generated_at=generated_at,
                market=market,
                universe_count=universe_count,
            )
            key = _history_key(record)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            new_records.append(record)

    if new_records:
        with path.open("a", encoding="utf-8") as f:
            for record in new_records:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    return len(new_records)


def format_recommendation_console(final_df: pd.DataFrame, limit: int | None = None) -> str:
    """Format Recommendation Layer details for console output."""
    from backend.alphaforge_policy import infer_recommendation

    if final_df is None or final_df.empty:
        return "[Recommendation Layer]\n  결과 없음"

    df = final_df.copy()

    # 1. Row-level infer_recommendation
    recs = []
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        row_dict.update(infer_recommendation(row_dict))
        recs.append(row_dict)

    df_recs = pd.DataFrame(recs)

    # 2. Ranking Calculation
    action_priority = {"BUY_NOW": 5, "CONDITIONAL_BUY": 4, "STARTER_POSITION": 3, "WATCH_ONLY": 2, "AVOID": 1}
    df_recs["_action_priority"] = df_recs["recommendation_action"].map(action_priority).fillna(0)
    df_recs = df_recs.sort_values(by=["_action_priority", "recommendation_score"], ascending=[False, False])

    df_recs["recommendation_rank"] = None
    top_mask = df_recs["recommendation_action"].isin({"BUY_NOW", "CONDITIONAL_BUY", "STARTER_POSITION"})
    top_indices = df_recs[top_mask].index[:3]
    for idx, i in enumerate(top_indices):
        df_recs.at[i, "recommendation_rank"] = idx + 1

    df_recs = df_recs.drop(columns=["_action_priority"])

    rows = df_recs.head(limit) if limit else df_recs

    lines = ["[Recommendation Layer 상세 결과]"]
    for _, row in rows.iterrows():
        symbol = row.get("code") or row.get("symbol") or "-"
        name = row.get("name") or "-"
        action = row.get("recommendation_action") or "WATCH_ONLY"
        rank_val = row.get("recommendation_rank")
        rank_str = f"{rank_val}" if rank_val is not None else "N/A"
        score = int(row.get("recommendation_score") or 0)
        size = int(row.get("suggested_position_size") or 0)
        reason = row.get("recommendation_reason") or ""
        trigger = row.get("entry_trigger") or ""
        invalidation = row.get("invalidation_condition") or ""

        lines.append(f"종목: {name} ({symbol})")
        lines.append(f"추천: {action} | 순위 {rank_str} | 점수 {score} | 권장비중 {size}%")
        lines.append(f"추천 사유: {reason}")
        lines.append(f"진입 트리거: {trigger}")
        lines.append(f"무효화 조건: {invalidation}")
        lines.append("")

    top_rows = df_recs[df_recs["recommendation_rank"].notna()].sort_values("recommendation_rank")

    lines.append("[오늘의 추천 TOP 3]")
    if top_rows.empty:
        lines.append("  추천 후보 없음. 조건부/소액탐색 후보만 존재")
    else:
        has_buy_now = any(top_rows["recommendation_action"] == "BUY_NOW")
        for idx, (_, row) in enumerate(top_rows.iterrows(), 1):
            name = row.get("name") or "-"
            action = row.get("recommendation_action") or "WATCH_ONLY"
            size = int(row.get("suggested_position_size") or 0)
            score = int(row.get("recommendation_score") or 0)
            lines.append(f"{idx}. {name} — {action} / {size}% / 점수 {score}")
        if not has_buy_now:
            lines.append("BUY_NOW 없음. 조건부/소액탐색 후보만 존재")

    return "\n".join(lines)
