from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pandas as pd


def _records(df: pd.DataFrame, limit: int = 30) -> list[dict[str, Any]]:
    from backend.alphaforge_policy import normalize_result_schema
    if df is None or df.empty:
        return []
    rows = df.head(limit).where(pd.notna(df), None).to_dict(orient="records")
    return [normalize_result_schema(r) for r in rows]


def _find_output(outputs: dict[str, pd.DataFrame], node_logs: list[Any], node_type: str) -> pd.DataFrame | None:
    for log in reversed(node_logs):
        if getattr(log, "node_type", None) == node_type and log.node_id in outputs:
            return outputs[log.node_id]
    return None


def _final_dataframe(outputs: dict[str, pd.DataFrame], node_logs: list[Any]) -> pd.DataFrame:
    score_df = _find_output(outputs, node_logs, "score_filter")
    if score_df is not None:
        return score_df.copy()
    if node_logs:
        last_id = node_logs[-1].node_id
        if last_id in outputs:
            return outputs[last_id].copy()
    return pd.DataFrame()


def _top_nan_columns(df: pd.DataFrame, limit: int = 10) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []

    # 제외할 설명용 및 레거시 컬럼들
    exclude_terms = (
        "warning", "reason", "note", "comment", "message", "label", "flag",
        "tier", "bucket", "status", "display", "candidates"
    )

    core_cols = [
        c for c in df.columns
        if not any(t in str(c).lower() for t in exclude_terms)
    ]

    # 핵심 수치 컬럼 우선 순위 (이 중 NaN이 있으면 품질 문제)
    priority_cols = {
        "rs_percentile", "rs_rating", "rs_score", "vcp_score", "breakout_score",
        "flow_score", "total_score", "final_score",
        "close", "volume", "market_cap", "primary_bucket"
    }
    # [Refinement] 핵심 컬럼 진단 대상을 명시적인 priority_cols로 제한 (raw_trading_value 등 제외)
    core_cols = [c for c in df.columns if c in priority_cols]

    counts = df[core_cols].isna().sum().sort_values(ascending=False)
    return [
        {"column": str(col), "nan_count": int(count), "ratio": round(int(count) / len(df), 4)}
        for col, count in counts.head(limit).items()
        if int(count) > 0
    ]


def _top_illiquid(df: pd.DataFrame, limit: int = 12) -> list[dict[str, Any]]:
    if df is None or df.empty or "liquidity_status" not in df.columns:
        return []
    # ILLIQUID 상태인 종목들을 거래대금 낮은 순으로 추출
    illiquid = df[df["liquidity_status"] == "ILLIQUID"].copy()
    if illiquid.empty: return []

    illiquid = illiquid.sort_values("liquidity_trading_value", ascending=True)
    return [
        {
            "symbol": str(row.get("code", "")),
            "name": str(row.get("name", "")),
            "liquidity_trading_value": float(row.get("liquidity_trading_value", 0)),
            "threshold": float(row.get("liquidity_threshold", 0)),
            "reason": str(row.get("liquidity_reason", ""))
        }
        for _, row in illiquid.head(limit).iterrows()
    ]


def _data_missing_ratio(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return 0.0
    marker_cols = [c for c in df.columns if any(t in c.lower() for t in ("flag", "status"))]
    if not marker_cols:
        return 0.0
    mask = df[marker_cols].apply(
        lambda s: s.astype(str).str.contains("DATA_MISSING|데이터 없음|수집 실패|UNKNOWN", case=False, na=False)
    )
    val = mask.any(axis=1).mean()
    return round(float(val), 4) if pd.notna(val) else 0.0


def build_analysis_payload(result: Any, node_results: dict[str, dict[str, Any]], as_of_date: str = "") -> dict[str, Any]:
    outputs: dict[str, pd.DataFrame] = result.outputs
    logs = result.node_logs
    final_df = _final_dataframe(outputs, logs)

    universe_df = _find_output(outputs, logs, "universe")
    universe_count = len(universe_df) if universe_df is not None else 0

    liquidity_df = _find_output(outputs, logs, "liquidity_filter")
    suspicious_liquidity_records = []
    if liquidity_df is not None and hasattr(liquidity_df, "attrs"):
        suspicious_liquidity_records = liquidity_df.attrs.get("suspicious_liquidity_records", [])

    if not final_df.empty and "total_score" in final_df.columns:
        final_df = final_df.sort_values("total_score", ascending=False).reset_index(drop=True)

    bucket_col = "primary_bucket" if "primary_bucket" in final_df else ("candidate_status" if "candidate_status" in final_df else "")

    primary_counts = {
        "TIER_1": 0, "TIER_2": 0, "TIER_3": 0, "WATCHLIST": 0, "CRISIS_HOLD": 0, "REJECTED": 0
    }
    if not final_df.empty and bucket_col and bucket_col in final_df:
        counts = final_df[bucket_col].value_counts().to_dict()
        for k in primary_counts:
            primary_counts[k] = int(counts.get(k, 0))

    # [Refinement] denominator가 0인 경우를 방지하는 safePercent 헬퍼
    def safePercent(num, den):
        if not den or den == 0: return 0.0
        return round((num / den) * 100, 1)

    primary_total_count = int(sum(primary_counts.values()))
    filtered_count = max(universe_count - len(final_df), 0)

    w_flag_col = "watchlist_flag" if "watchlist_flag" in final_df else None
    watchlist_flag_true = int(final_df[w_flag_col].fillna(False).astype(bool).sum()) if w_flag_col else 0
    watchlist_flag_false = len(final_df) - watchlist_flag_true
    if final_df.empty:
        label_series = pd.Series(dtype=str)
    elif "final_label" in final_df:
        label_series = final_df["final_label"].fillna("").astype(str)
    elif "display_label" in final_df:
        label_series = final_df["display_label"].fillna("").astype(str)
    else:
        try:
            from backend.alphaforge_policy import infer_final_label
            label_series = final_df.apply(lambda row: infer_final_label(row.to_dict()), axis=1).astype(str)
        except Exception:
            label_series = pd.Series([""] * len(final_df), index=final_df.index)

    priority_watch_count = int(label_series.eq("PRIORITY_WATCH").sum()) if len(label_series) else 0
    risk_watch_count = int(label_series.eq("RISK_WATCH").sum()) if len(label_series) else 0
    setup_watch_count = int(label_series.eq("SETUP_WATCH").sum()) if len(label_series) else 0
    near_buy_count = int(label_series.eq("NEAR_BUY").sum()) if len(label_series) else 0
    buy_candidate_count = int(label_series.eq("BUY_CANDIDATE").sum()) if len(label_series) else 0

    node_counts = []
    most_aggressive = None
    node_role_map = {
        "universe": "LOAD",
        "liquidity_filter": "CHECK",
        "vcp": "CLASSIFY",
        "box_breakout": "CHECK",
        "ma_alignment": "CHECK",
        "foreign_flow": "CHECK",
        "institution_flow": "CHECK",
        "rs_rating": "CHECK",
        "sector": "CONTEXT",
        "macro_filter": "CONTEXT",
        "score_filter": "HARD_GATE",
        "top_n": "RANK",
    }
    # [Refinement] NaN 진단에서 제외할 컬럼 리스트 강화
    exclude_terms = (
        "warning", "reason", "note", "comment", "message", "label", "flag",
        "tier", "bucket", "status", "display", "candidates", "primary_bucket", "candidate_status"
    )

    for log in logs:
        # 각 노드의 nan_columns 필터링
        filtered_nans = [
            n for n in getattr(log, "nan_columns", [])
            if not any(t in str(n.get("column", "")).lower() for t in exclude_terms)
        ]

        item = {
            "node_id": log.node_id,
            "node_type": log.node_type,
            "node_role": node_role_map.get(log.node_type, "CHECK"),
            "input_count": log.input_count,
            "output_count": log.output_count,
            "dropped_count": getattr(log, "dropped_count", max(log.input_count - log.output_count, 0)),
            "elapsed_ms": round(log.latency_ms, 1),
            "cache_hit": log.cache_hit,
            "missing_ratio": round(getattr(log, "data_missing_ratio", 0.0), 4),
            "data_missing_ratio": round(getattr(log, "data_missing_ratio", 0.0), 4),
            "nan_columns": filtered_nans,
        }
        node_counts.append(item)
        if most_aggressive is None or item["dropped_count"] > most_aggressive["dropped_count"]:
            most_aggressive = item

    def _agg_reasons(col_name: str, sep: str = ",") -> list[dict[str, Any]]:
        if col_name not in final_df: return []
        counter: Counter[str] = Counter()
        for raw in final_df[col_name].dropna():
            # 리스트, numpy array 및 문자열 모두 대응
            if isinstance(raw, (list, tuple, np.ndarray)):
                reasons = [str(r).strip() for r in raw if str(r).strip()]
            else:
                reasons = [item.strip() for item in str(raw).replace(" / ", sep).split(sep) if item.strip()]

            for reason in reasons:
                counter[reason] += 1
        return [{"reason": r, "count": int(c)} for r, c in counter.most_common(12)]

    def _get_dist(col: str):
        if col not in final_df: return []
        counts = final_df[col].dropna().astype(str).value_counts().head(10)
        return [{"value": str(v), "count": int(c)} for v, c in counts.items()]

    def _first_value(col: str, default=None):
        if col not in final_df or final_df.empty:
            return default
        vals = final_df[col].dropna()
        return vals.iloc[0] if not vals.empty else default

    def _plain(value):
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, dict):
            return dict(value)
        if pd.isna(value) if not isinstance(value, (list, tuple, dict, np.ndarray)) else False:
            return None
        return value

    market_regime = {
        "RISK_ON": int(_first_value("risk_on_prob", 20) or 0),
        "NEUTRAL": int(_first_value("neutral_prob", 45) or 0),
        "RISK_OFF": int(_first_value("risk_off_prob", 25) or 0),
        "CRISIS": int(_first_value("crisis_prob", 10) or 0),
        "dominant_regime": _plain(_first_value("dominant_regime", "NEUTRAL")),
        "secondary_regime": _plain(_first_value("secondary_regime", "RISK_OFF")),
        "as_of": _plain(_first_value("regime_as_of", "")),
        "data_sources": _plain(_first_value("regime_data_sources", [])),
        "data_status": _plain(_first_value("regime_data_status", "일부 결측")),
        "missing_inputs": _plain(_first_value("regime_missing_inputs", [])),
    }

    diagnostics = {
        "node_counts": node_counts,
        "most_aggressive_filter_node": most_aggressive,
        "pipeline_diagnostic_counts": {
            "Hard Drop": int(filtered_count),
            "Soft Hold": int(primary_counts["CRISIS_HOLD"] + primary_counts["WATCHLIST"]),
            "Core Candidate": int(primary_counts["TIER_1"] + primary_counts["TIER_2"]),
            "Alert Candidate": int(watchlist_flag_true),
            "Data Insufficient": int(final_df["gate_status"].eq("HOLD").sum()) if "gate_status" in final_df else 0,
            "Data Unit Warning": int(final_df["data_unit_check"].eq("DATA_UNIT_WARNING").sum()) if "data_unit_check" in final_df else 0,
        },

        # 분포 (Distributions)
        # 분포 (Distributions)
        "vcp_status_distribution": _get_dist("vcp_status"),
        "vcp_raw_score_distribution": [
            {"range": "90-100", "count": int(final_df["vcp_raw_score"].between(90, 100).sum())},
            {"range": "75-89", "count": int(final_df["vcp_raw_score"].between(75, 89).sum())},
            {"range": "0-74", "count": int(final_df["vcp_raw_score"].lt(75).sum())},
        ] if "vcp_raw_score" in final_df else [],
        "vcp_effective_score_distribution": [
            {"range": "90-100", "count": int(final_df["vcp_effective_score"].between(90, 100).sum())},
            {"range": "75-89", "count": int(final_df["vcp_effective_score"].between(75, 89).sum())},
            {"range": "0-74", "count": int(final_df["vcp_effective_score"].lt(75).sum())},
        ] if "vcp_effective_score" in final_df else [],
        "breakout_status_distribution": _get_dist("breakout_status"),
        "rs_status_distribution": _get_dist("rs_status"),
        "primary_bucket_distribution": _get_dist(bucket_col),
        "watch_alert_type_distribution": _get_dist("watch_alert_type"),
        "display_label_distribution": _get_dist("final_label") or _get_dist("display_label") or _get_dist("display_watch_alert_type"),
        "candidate_confidence_distribution": _get_dist("candidate_confidence"),
        "policy_violation_count": int(final_df["policy_violation_count"].sum()) if "policy_violation_count" in final_df else 0,
        "policy_violation_records": _records(final_df[final_df["policy_violation_count"] > 0][["code", "name", "policy_violation_records"]], 10) if "policy_violation_count" in final_df else [],

        # [Refinement] 신규 진단 심볼 리스트
        "action_alert_symbols": final_df[final_df["watch_alert_type"] == "ACTION_ALERT"]["code"].tolist() if "watch_alert_type" in final_df else [],
        "risk_watch_symbols": final_df[final_df["watch_alert_type"] == "RISK_WATCH"]["code"].tolist() if "watch_alert_type" in final_df else [],
        "data_review_alert_symbols": final_df[final_df["watch_alert_type"] == "DATA_REVIEW"]["code"].tolist() if "watch_alert_type" in final_df else [],
        "high_vcp_low_rs_symbols": final_df[final_df["vcp_cross_warning"] == "LOW_RS_HIGH_VCP"]["code"].tolist() if "vcp_cross_warning" in final_df else [],
        "high_vcp_rejected_symbols": final_df[final_df["vcp_cross_warning"] == "HIGH_VCP_REJECTED_BY_HARD_GATE"]["code"].tolist() if "vcp_cross_warning" in final_df else [],
        "reverse_expansion_symbols": final_df[final_df["vcp_status"] == "REVERSE_EXPANSION"]["code"].tolist() if "vcp_status" in final_df else [],
        "rally_exhaustion_symbols": final_df[final_df["vcp_status"] == "RALLY_EXHAUSTION"]["code"].tolist() if "vcp_status" in final_df else [],
        "data_unit_warning_symbols": final_df[final_df["data_unit_check"] == "DATA_UNIT_WARNING"]["code"].tolist() if "data_unit_check" in final_df else [],
        "liquidity_uncertain_symbols": final_df[final_df["liquidity_status"] == "LIQUIDITY_UNCERTAIN"]["code"].tolist() if "liquidity_status" in final_df else [],

        # 사유 리스트 (Reason lists)
        "tier_promotion_reasons": _agg_reasons("tier_promotion_reasons"),
        "promotion_reasons": _agg_reasons("promotion_reasons"),
        "display_promotion_reasons": _agg_reasons("display_promotion_reasons"),
        "display_rejected_reasons": _agg_reasons("display_rejected_reasons"),
        "display_watch_alert_reasons": _agg_reasons("display_watch_alert_reasons"),
        "display_restriction_reasons": _agg_reasons("display_restriction_reasons"),
        "failed_buy_gates": _agg_reasons("failed_buy_gates"),
        "watchlist_reasons": _agg_reasons("watchlist_reasons"),
        "tier_downgrade_reasons": _agg_reasons("tier_downgrade_reasons"),
        "rejected_reasons": _agg_reasons("rejected_reasons"),
        "risk_watch_reasons": _agg_reasons("risk_watch_reasons"),
        "watchlist_flag_reasons": _agg_reasons("watch_alert_reasons"),
        "watch_exclusion_reasons": _agg_reasons("watch_alert_exclusion_reasons"),
        "risk_gate_reasons": _agg_reasons("risk_gate_reasons"),
        "hard_gate_reasons": _agg_reasons("hard_gate_reasons"),
        "nan_columns": _top_nan_columns(final_df),
        "data_missing_ratio": _data_missing_ratio(final_df),
        "data_quality_warnings": [],

        # 유동성 진단 (Liquidity Diagnostics)
        "liquidity_status_distribution": _get_dist("liquidity_status"),
        "volume_source_distribution": _get_dist("liquidity_trading_value_source"),
        "liquidity_quote_source_distribution": _get_dist("liquidity_quote_source"),
        "liquidity_close_source_distribution": _get_dist("liquidity_close_source"),
        "volume_suspicious_count": int(final_df["volume_suspicious"].fillna(False).astype(bool).sum()) if "volume_suspicious" in final_df else 0,
        "liquidity_warning_count": int(final_df["liquidity_data_warning"].notna().sum()) if "liquidity_data_warning" in final_df else 0,
        "liquidity_data_warnings": _records(final_df[final_df["liquidity_data_warning"].notna()][["code", "name", "liquidity_data_warning"]], 20) if "liquidity_data_warning" in final_df else [],
        "top_illiquid_symbols": _top_illiquid(final_df),
        "suspicious_liquidity_records": suspicious_liquidity_records,
        "hyosung_trace": universe_df.attrs.get("hyosung_trace", {}) if universe_df is not None and hasattr(universe_df, "attrs") else {},
    }

    # --- Policy Validation Diagnostics (post-run invariant check) ---
    try:
        from backend.alphaforge_policy import validate_policy_invariants
        rows_for_validation = final_df.where(pd.notna(final_df), None).to_dict(orient="records") if not final_df.empty else []
        policy_diag = validate_policy_invariants(rows_for_validation)
        diagnostics.update({
            "policy_violation_count": policy_diag["policy_violation_count"],
            "policy_violation_records": policy_diag["policy_violation_records"],
            "missing_required_field_count": policy_diag["missing_required_field_count"],
            "missing_required_field_records": policy_diag["missing_required_field_records"],
            "score_max_inconsistent_count": policy_diag["score_max_inconsistent_count"],
            "score_max_inconsistent_records": policy_diag["score_max_inconsistent_records"],
            "empty_display_reason_count": policy_diag["empty_display_reason_count"],
            "empty_display_reason_records": policy_diag["empty_display_reason_records"],
            "missing_display_promotion_reason_count": policy_diag["missing_display_promotion_reason_count"],
            "missing_display_promotion_reason_records": policy_diag["missing_display_promotion_reason_records"],
            "missing_display_rejected_reason_count": policy_diag["missing_display_rejected_reason_count"],
            "missing_display_rejected_reason_records": policy_diag["missing_display_rejected_reason_records"],
            "watch_alert_type_missing_count": policy_diag["watch_alert_type_missing_count"],
            "watch_alert_type_missing_records": policy_diag["watch_alert_type_missing_records"],
            "reason_code_untranslated_count": policy_diag["reason_code_untranslated_count"],
            "reason_code_untranslated_records": policy_diag["reason_code_untranslated_records"],
            "duplicate_reason_cleanup_count": policy_diag["duplicate_reason_cleanup_count"],
            "duplicate_reason_cleanup_records": policy_diag["duplicate_reason_cleanup_records"],
            "vcp_raw_missing_count": policy_diag["vcp_raw_missing_count"],
            "vcp_raw_missing_records": policy_diag["vcp_raw_missing_records"],
            "watch_alert_type_distribution": policy_diag["watch_alert_type_distribution"],
            "candidate_confidence_distribution": policy_diag["candidate_confidence_distribution"],
            "vcp_cross_warning_distribution": policy_diag["vcp_cross_warning_distribution"],
        })
    except Exception as e:
        diagnostics["policy_validation_error"] = str(e)
        diagnostics["policy_violation_count"] = 0

    # 경고 및 품질 체크 추가
    if final_df.empty:
        diagnostics["data_quality_warnings"].append("최종 분석 결과가 0행입니다.")

    # TIER_2가 있는데 승격 사유가 비어있는지 체크
    if primary_counts["TIER_2"] > 0 and not diagnostics["tier_promotion_reasons"]:
        diagnostics["data_quality_warnings"].append("TIER_2 exists but promotion_reasons are empty")

    if universe_count >= 100 and primary_counts["REJECTED"] == 0:
        diagnostics["data_quality_warnings"].append("Rejected 후보가 0개입니다. 기준이 너무 느슨할 수 있습니다.")

    # 정합성 체크
    if primary_total_count != len(final_df):
        diagnostics["data_quality_warnings"].append(f"Primary Bucket 합계({primary_total_count})가 결과 행 수({len(final_df)})와 불일치합니다.")

    if most_aggressive and most_aggressive["dropped_count"] > 0:
        diagnostics["data_quality_warnings"].append(f"{most_aggressive['node_id']} 노드에서 {most_aggressive['dropped_count']}개가 누락되었습니다.")
    if diagnostics.get("missing_display_rejected_reason_count", 0) > 0:
        diagnostics["data_quality_warnings"].append("REJECTED 종목 중 제외 사유 표시가 비어 있습니다.")
    if diagnostics.get("watch_alert_type_missing_count", 0) > 0:
        diagnostics["data_quality_warnings"].append("관찰 라벨이 켜졌지만 legacy alert type이 비어 있는 종목이 있습니다.")
    if diagnostics.get("reason_code_untranslated_count", 0) > 0:
        diagnostics["data_quality_warnings"].append("사용자 화면에 코드형 reason이 남아 있을 수 있습니다.")
    if diagnostics.get("vcp_raw_missing_count", 0) > 0:
        diagnostics["data_quality_warnings"].append("vcp_raw_score_unavailable")

    summary = {
        "universe_count": universe_count,
        "tier1_count": primary_counts["TIER_1"],
        "tier2_count": primary_counts["TIER_2"],
        "tier3_count": primary_counts["TIER_3"],
        "watchlist_count": primary_counts["WATCHLIST"],
        "crisis_hold_count": primary_counts["CRISIS_HOLD"],
        "rejected_count": primary_counts["REJECTED"],
        "filtered_count": filtered_count,
        "primary_total_count": primary_total_count,

        # [Refinement] 신규 UI 라벨 대응 필드
        "total_analyzed_count": universe_count,
        "classification_completed_count": primary_total_count,
        "core_candidate_count": primary_counts["TIER_1"] + primary_counts["TIER_2"],
        "final_rejected_count": primary_counts["REJECTED"],
        "intermediate_filtered_count": filtered_count,
        "intermediate_filtered_rate": safePercent(filtered_count, universe_count),
        "final_rejected_rate": safePercent(primary_counts["REJECTED"], universe_count),
        "watch_alert_rate": safePercent(watchlist_flag_true, universe_count),

        "primary_count_total": primary_total_count,  # Compatibility
        "classified_count": primary_total_count,     # Compatibility
        "primary_counts": primary_counts,            # Compatibility
        "watchlist_flag_true_count": watchlist_flag_true,
        "watchlist_flag_false_count": watchlist_flag_false,
        "watchlist_flag_count": watchlist_flag_true, # Compatibility
        "watch_alert_count": watchlist_flag_true,
        "action_alert_count": int(final_df["watch_alert_type"].eq("ACTION_ALERT").sum()) if "watch_alert_type" in final_df else 0,
        "buy_candidate_count": buy_candidate_count,
        "hard_gate_buy_candidate_count": buy_candidate_count,
        "priority_watch_count": priority_watch_count,
        "risk_watch_count": risk_watch_count,
        "setup_watch_count": setup_watch_count,
        "near_buy_count": near_buy_count,
        "buy_candidate_message": "하드 게이트 통과 매수 후보" if buy_candidate_count > 0 else "",
        "no_buy_candidate_message": "오늘 실전 매수 후보 없음" if buy_candidate_count == 0 else "",
        "watch_only_message": "현재 결과는 관찰 후보이며 자동 매수 신호가 아닙니다." if buy_candidate_count == 0 else "",
        "market_regime": market_regime,
    }

    from backend.performance_tracker import get_performance_summary
    perf_date = as_of_date if as_of_date else (getattr(result, "as_of_date", None) or "")
    perf_summary = get_performance_summary(final_df, perf_date)
    summary["performance_summary"] = perf_summary

    # ── Recommendation Layer Ranking ──
    if not final_df.empty:
        from backend.alphaforge_policy import infer_recommendation
        if "recommendation_action" not in final_df.columns:
            recs = []
            for _, row in final_df.iterrows():
                recs.append(infer_recommendation(row.to_dict()))
            rec_df = pd.DataFrame(recs, index=final_df.index)
            # Avoid duplicate columns if any other fields exist
            for col in rec_df.columns:
                if col in final_df.columns:
                    final_df = final_df.drop(columns=[col])
            final_df = pd.concat([final_df, rec_df], axis=1)

        action_priority = {"BUY_NOW": 5, "CONDITIONAL_BUY": 4, "STARTER_POSITION": 3, "WATCH_ONLY": 2, "AVOID": 1}
        final_df["_action_priority"] = final_df["recommendation_action"].map(action_priority).fillna(0)

        # 정렬: 액션 우선순위 내림차순, 추천 점수 내림차순
        final_df = final_df.sort_values(by=["_action_priority", "recommendation_score"], ascending=[False, False])

        final_df["recommendation_rank"] = None
        # BUY_NOW, CONDITIONAL_BUY, STARTER_POSITION 등 상위 3개에 랭크 부여
        top_mask = final_df["recommendation_action"].isin({"BUY_NOW", "CONDITIONAL_BUY", "STARTER_POSITION"})
        top_indices = final_df[top_mask].index[:3]
        for idx, i in enumerate(top_indices):
            final_df.at[i, "recommendation_rank"] = idx + 1

        final_df = final_df.drop(columns=["_action_priority"])

        # summary에 주입
        top_recs = []
        for _, row in final_df[final_df["recommendation_rank"].notna()].sort_values("recommendation_rank").iterrows():
            top_recs.append({
                "code": str(row.get("code", "")),
                "name": str(row.get("name", "")),
                "action": str(row.get("recommendation_action", "")),
                "score": int(row.get("recommendation_score", 0)),
                "rank": int(row.get("recommendation_rank", 0)),
                "reason": str(row.get("recommendation_reason", "")),
                "trigger": str(row.get("entry_trigger", "")),
                "size": int(row.get("suggested_position_size", 0))
            })
        summary["top_recommendations"] = top_recs
        has_buy_now = any(r["action"] == "BUY_NOW" for r in top_recs)
        if not has_buy_now:
            summary["top_recommendations_message"] = "BUY_NOW 없음. 조건부/소액탐색 후보만 존재"
        else:
            summary["top_recommendations_message"] = ""


    summary["operation_report"] = build_operation_report(
        final_df=final_df,
        summary=summary,
        diagnostics=diagnostics,
        perf_summary=perf_summary,
        market_regime=market_regime,
    )

    structured_results = {
        "tier1": _records(final_df[final_df[bucket_col] == "TIER_1"], 100) if bucket_col else [],
        "tier2": _records(final_df[final_df[bucket_col] == "TIER_2"], 100) if bucket_col else [],
        "tier3": _records(final_df[final_df[bucket_col] == "TIER_3"], 100) if bucket_col else [],
        "watchlist": _records(final_df[final_df[bucket_col] == "WATCHLIST"], 100) if bucket_col else [],
        "crisis_hold": _records(final_df[final_df[bucket_col] == "CRISIS_HOLD"], 100) if bucket_col else [],
        "rejected": _records(final_df[final_df[bucket_col] == "REJECTED"], 100) if bucket_col else [],
        "fallback_candidates": {
            "top_score_candidates": _records(final_df.sort_values("total_score", ascending=False).head(5))
        },
    }

    return {
        "summary": summary,
        "diagnostics": diagnostics,
        "results": structured_results,
        "node_results": node_results,
    }


def build_operation_report(
    final_df: pd.DataFrame,
    summary: dict[str, Any],
    diagnostics: dict[str, Any],
    perf_summary: dict[str, Any],
    market_regime: dict[str, Any],
) -> dict[str, Any]:
    """시스템 출력 품질 기반 운영 리포트 생성. 투자 점수가 아닌 파이프라인 품질 지표."""

    buy_candidate_count = summary.get("buy_candidate_count", 0)
    near_buy_count = summary.get("near_buy_count", 0)
    priority_watch_count = summary.get("priority_watch_count", 0)
    risk_watch_count = summary.get("risk_watch_count", 0)
    rejected_count = summary.get("rejected_count", 0)
    market_mode = str(market_regime.get("dominant_regime", "NEUTRAL"))
    perf_status = perf_summary.get("status", "DATA_INSUFFICIENT")

    # ── Top blocking reasons from failed_buy_gates ──────────────────────────
    top_blocking_reasons: list[dict[str, Any]] = []
    if not final_df.empty and "failed_buy_gates" in final_df.columns:
        gate_counter: Counter[str] = Counter()
        for raw in final_df["failed_buy_gates"].dropna():
            if isinstance(raw, (list, tuple, np.ndarray)):
                items = [str(r).strip() for r in raw if str(r).strip()]
            else:
                items = [x.strip() for x in str(raw).replace(", ", ",").split(",") if x.strip()]
            for item in items:
                gate_counter[item] += 1
        top_blocking_reasons = [
            {"reason": r, "count": int(c)} for r, c in gate_counter.most_common(5)
        ]

    # ── Quality Score (0~100, system output quality) ────────────────────────
    score = 50  # neutral base

    # Positive signals
    if buy_candidate_count > 0 or near_buy_count > 0:
        score += 10  # clear buy-tier labels exis
    if not final_df.empty and "failed_buy_gates" in final_df.columns:
        gates_recorded = final_df["failed_buy_gates"].apply(
            lambda x: isinstance(x, list) and len(x) > 0
        ).sum()
        if gates_recorded > 0:
            score += 10  # gate audit trail presen
    if perf_status == "READY":
        score += 10  # performance data available
    if not final_df.empty and "vcp_component_scores" in final_df.columns:
        has_component = final_df["vcp_component_scores"].apply(
            lambda x: isinstance(x, dict) and len(x) > 0
        ).any()
        if has_component:
            score += 5  # VCP breakdown presen
    if not final_df.empty and "final_label" in final_df.columns:
        score += 5  # labels assigned

    # Negative signals (deductions)
    if final_df.empty:
        score -= 30  # no output at all
    else:
        missing_label_ratio = final_df["final_label"].isna().mean() if "final_label" in final_df.columns else 1.0
        if missing_label_ratio > 0.5:
            score -= 15  # >50% rows have no label
        missing_close_ratio = final_df["close"].isna().mean() if "close" in final_df.columns else 0.0
        if missing_close_ratio > 0.3:
            score -= 10  # >30% rows missing close price
    if not final_df.empty and "vcp_component_scores" not in final_df.columns:
        score -= 5  # no VCP component breakdown
    if perf_status == "DATA_INSUFFICIENT":
        score -= 5  # no performance history ye

    quality_score = max(0, min(100, score))

    # ── Operator message ────────────────────────────────────────────────────
    if final_df.empty:
        operator_message = "분석 결과가 없습니다. 파이프라인 설정 및 데이터 상태를 확인하세요."
        status = "DATA_INSUFFICIENT"
    elif perf_status == "DATA_INSUFFICIENT" and buy_candidate_count == 0 and near_buy_count == 0:
        operator_message = "성과 추적 데이터가 아직 부족합니다. 통계 판단은 보류하고 관찰 후보 중심으로 감시하세요."
        status = "DATA_INSUFFICIENT"
    elif buy_candidate_count > 0:
        operator_message = (
            f"하드 게이트를 통과한 매수 후보 {buy_candidate_count}개가 있습니다. "
            "실패 게이트와 시장 국면을 함께 확인하세요."
        )
        status = "READY"
    elif near_buy_count > 0:
        operator_message = (
            f"실전 매수 후보는 없으나 NEAR_BUY {near_buy_count}개가 있습니다. "
            "하드 게이트 탈락 사유를 점검하세요."
        )
        status = "READY"
    else:
        operator_message = "오늘 실전 매수 후보 없음. 관찰 후보 중심으로 감시하세요."
        status = "READY"

    return {
        "status": status,
        "market_mode": market_mode,
        "buy_candidate_count": buy_candidate_count,
        "near_buy_count": near_buy_count,
        "priority_watch_count": priority_watch_count,
        "risk_watch_count": risk_watch_count,
        "rejected_count": rejected_count,
        "top_blocking_reasons": top_blocking_reasons,
        "quality_score": quality_score,
        "operator_message": operator_message,
    }
