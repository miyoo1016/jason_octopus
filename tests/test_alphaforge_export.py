import json

import pandas as pd

from backend.alphaforge_export import (
    add_dual_horizon_fields,
    export_alphaforge_candidates,
    export_alphaforge_daily_history,
    export_alphaforge_dual_horizon,
    format_dual_horizon_console,
)


def test_export_alphaforge_candidates_fills_to_limit_without_rejected(tmp_path):
    df = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "액션",
                "primary_bucket": "TIER_2",
                "watch_alert_type": "ACTION_ALERT",
                "rs_rating": 91.5,
                "vcp_status": "VCP_VALID",
                "box_upper_price": 12345,
                "total_score": 155,
                "score_max": 210,
            },
            {
                "code": "000002",
                "name": "관찰",
                "primary_bucket": "TIER_3",
                "watch_alert_type": "SETUP_WATCH",
                "rs_rating": 80,
                "vcp_status": "VCP_WARNING",
                "pivot_price": 67890,
                "total_score": 118,
                "score_max": 210,
            },
            {
                "code": "000003",
                "name": "제외",
                "primary_bucket": "WATCHLIST",
                "watch_alert_type": "RISK_WATCH",
                "rs_rating": 70,
                "vcp_status": "REVERSE_EXPANSION",
                "total_score": 90,
                "short_swing_score": 45,
                "position_swing_score": 35,
                "trading_value": 20_000_000_000,
                "score_max": 210,
            },
            {
                "code": "000005",
                "name": "진짜제외",
                "primary_bucket": "REJECTED",
                "watch_alert_type": "RISK_WATCH",
                "rs_rating": 99,
                "vcp_status": "VCP_VALID",
                "total_score": 200,
                "score_max": 210,
            },
        ]
    )
    export_path = tmp_path / "alphaforge_candidates.json"

    count = export_alphaforge_candidates(df, export_path, generated_at="2026-05-10T12:00:00")

    assert count == 3
    records = json.loads(export_path.read_text(encoding="utf-8"))
    assert [r["symbol"] for r in records] == ["000001", "000002", "000003"]
    assert "000005" not in [r["symbol"] for r in records]
    assert set(records[0]) == {
        "symbol",
        "name",
        "tier",
        "alert_type",
        "rs",
        "vcp_status",
        "box_upper_price",
        "total_score",
        "generated_at",
    }
    assert records[0]["tier"] == "TIER_2"
    assert records[0]["box_upper_price"] == 12345
    assert records[1]["tier"] == "TIER_3"
    assert records[1]["box_upper_price"] == 67890


def test_export_alphaforge_candidates_uses_ranked_fallback_up_to_five(tmp_path):
    df = pd.DataFrame(
        [
            {
                "code": "000011",
                "name": "핵심1",
                "primary_bucket": "TIER_3",
                "watch_alert_type": "SETUP_WATCH",
                "rs_rating": 80,
                "vcp_status": "VCP_WARNING",
                "total_score": 120,
                "score_max": 210,
            },
            {
                "code": "000012",
                "name": "핵심2",
                "primary_bucket": "TIER_2",
                "watch_alert_type": "NONE",
                "rs_rating": 82,
                "vcp_status": "VCP_VALID",
                "total_score": 118,
                "score_max": 210,
            },
            {
                "code": "000013",
                "name": "보충상",
                "primary_bucket": "WATCHLIST",
                "watch_alert_type": "RISK_WATCH",
                "rs_rating": 75,
                "vcp_status": "VCP_WARNING",
                "total_score": 100,
                "short_swing_score": 40,
                "position_swing_score": 30,
                "trading_value": 10_000_000_000,
                "score_max": 210,
            },
            {
                "code": "000014",
                "name": "보충중",
                "primary_bucket": "WATCHLIST",
                "watch_alert_type": "SETUP_WATCH",
                "rs_rating": 90,
                "vcp_status": "VCP_WARNING",
                "total_score": 90,
                "short_swing_score": 80,
                "position_swing_score": 80,
                "trading_value": 90_000_000_000,
                "score_max": 210,
            },
            {
                "code": "000015",
                "name": "보충하",
                "primary_bucket": "WATCHLIST",
                "watch_alert_type": "RISK_WATCH",
                "rs_rating": 60,
                "vcp_status": "BASE_BUILDING",
                "total_score": 85,
                "short_swing_score": 70,
                "position_swing_score": 70,
                "trading_value": 80_000_000_000,
                "score_max": 210,
            },
            {
                "code": "000016",
                "name": "여섯번째",
                "primary_bucket": "WATCHLIST",
                "watch_alert_type": "RISK_WATCH",
                "rs_rating": 99,
                "vcp_status": "VCP_VALID",
                "total_score": 80,
                "short_swing_score": 100,
                "position_swing_score": 100,
                "trading_value": 100_000_000_000,
                "score_max": 210,
            },
        ]
    )
    export_path = tmp_path / "alphaforge_candidates.json"

    count = export_alphaforge_candidates(df, export_path, generated_at="2026-05-10T12:00:00")

    records = json.loads(export_path.read_text(encoding="utf-8"))
    assert count == 5
    assert [r["symbol"] for r in records] == ["000011", "000012", "000013", "000014", "000015"]


def test_export_alphaforge_candidates_keeps_candidate_without_box_price(tmp_path):
    df = pd.DataFrame(
        [
            {
                "code": "000004",
                "name": "박스없음",
                "primary_bucket": "TIER_3",
                "watch_alert_type": "NONE",
                "rs_rating": 75,
                "vcp_status": "VCP_WARNING",
                "total_score": 110,
                "score_max": 210,
            },
        ]
    )
    export_path = tmp_path / "alphaforge_candidates.json"

    count = export_alphaforge_candidates(df, export_path, generated_at="2026-05-10T12:00:00")

    assert count == 1
    records = json.loads(export_path.read_text(encoding="utf-8"))
    assert records[0]["symbol"] == "000004"
    assert records[0]["box_upper_price"] is None


def test_export_alphaforge_daily_history_appends_target_tiers_without_duplicates(tmp_path):
    df = pd.DataFrame(
        [
            {
                "code": "000101",
                "name": "티어2",
                "primary_bucket": "TIER_2",
                "watch_alert_type": "ACTION_ALERT",
                "rs_rating": 88,
                "vcp_status": "VCP_VALID",
                "box_high": 50100,
                "total_score": 140,
                "liquidity_close": 49000,
                "display_promotion_reasons": ["강한 주도주 후보"],
                "risk_flags": [],
                "score_max": 210,
            },
            {
                "code": "000102",
                "name": "관찰",
                "primary_bucket": "TIER_3",
                "watch_alert_type": "SETUP_WATCH",
                "rs_rating": 77,
                "vcp_status": "VCP_WARNING",
                "pivot_price": 30100,
                "total_score": 115,
                "close": 29000,
                "watchlist_reasons": ["셋업 관찰"],
                "risk_flags": ["REVERSE_EXPANSION"],
                "score_max": 210,
            },
            {
                "code": "000103",
                "name": "추적",
                "primary_bucket": "WATCHLIST",
                "watch_alert_type": "RISK_WATCH",
                "rs_rating": 66,
                "vcp_status": "BASE_BUILDING",
                "total_score": 95,
                "score_max": 210,
            },
            {
                "code": "000104",
                "name": "제외",
                "primary_bucket": "REJECTED",
                "watch_alert_type": "NONE",
                "rs_rating": 30,
                "vcp_status": "NOT_READY",
                "total_score": 20,
                "display_rejected_reasons": ["핵심 조건 부족"],
                "score_max": 210,
            },
            {
                "code": "000105",
                "name": "티어1",
                "primary_bucket": "TIER_1",
                "watch_alert_type": "ACTION_ALERT",
                "rs_rating": 95,
                "vcp_status": "VCP_STRICT",
                "total_score": 180,
                "score_max": 210,
            },
        ]
    )
    history_path = tmp_path / "alphaforge_daily_signals.jsonl"

    first_count = export_alphaforge_daily_history(
        df,
        run_date="2026-05-11",
        generated_at="2026-05-11T12:00:00",
        market="ALL",
        universe_count=30,
        history_path=history_path,
    )
    second_count = export_alphaforge_daily_history(
        df,
        run_date="2026-05-11",
        generated_at="2026-05-11T12:01:00",
        market="ALL",
        universe_count=30,
        history_path=history_path,
    )

    records = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
    assert first_count == 4
    assert second_count == 0
    assert len(records) == 4
    assert {r["tier"] for r in records} == {"TIER_2", "TIER_3", "WATCHLIST", "REJECTED"}
    assert records[0]["box_upper_price"] == 50100
    assert records[1]["box_upper_price"] == 30100
    assert records[1]["close_price"] == 29000
    assert records[1]["risk_flags"] == ["REVERSE_EXPANSION"]


def test_export_alphaforge_dual_horizon_writes_all_rows_and_fields(tmp_path):
    df = pd.DataFrame(
        [
            {
                "code": "000201",
                "name": "겹침",
                "primary_bucket": "TIER_2",
                "watch_alert_type": "ACTION_ALERT",
                "change_pct": 0.025,
                "trading_value": 80_000_000_000,
                "breakout_status": "BREAKOUT_CONFIRMED",
                "breakout_distance_pct": 0.5,
                "breakout_volume_ratio": 1.8,
                "rs_rating": 92,
                "vcp_status": "VCP_VALID",
                "ma_alignment_flag": "ALIGNED",
                "flow_total_score": 28,
                "liquidity_status": "LIQUID",
                "score_max": 210,
            },
            {
                "code": "000202",
                "name": "위험",
                "primary_bucket": "REJECTED",
                "watch_alert_type": "NONE",
                "change_pct": -0.03,
                "trading_value": 5_000_000_000,
                "breakout_status": "FAILED_BREAKOUT",
                "rs_rating": 30,
                "vcp_status": "REVERSE_EXPANSION",
                "ma_alignment_flag": "NOT_ALIGNED",
                "flow_total_score": 0,
                "score_max": 210,
            },
        ]
    )
    export_path = tmp_path / "alphaforge_dual_horizon.json"

    count = export_alphaforge_dual_horizon(df, export_path)

    records = json.loads(export_path.read_text(encoding="utf-8"))
    assert count == 2
    assert len(records) == 2
    for record in records:
        assert "short_swing_score" in record
        assert "position_swing_score" in record
        assert "horizon_label" in record
        assert "short_reasons" in record
        assert "position_reasons" in record
    assert records[0]["horizon_label"] == "OVERLAP"
    assert records[1]["horizon_label"] == "RISK_ONLY"


def test_dual_horizon_console_format_includes_short_position_and_label():
    df = add_dual_horizon_fields(pd.DataFrame([{
        "code": "000301",
        "name": "표시",
        "primary_bucket": "TIER_3",
        "watch_alert_type": "SETUP_WATCH",
        "change_pct": 1.2,
        "breakout_status": "NEAR_BREAKOUT",
        "rs_rating": 80,
        "vcp_status": "VCP_WARNING",
        "score_max": 210,
    }]))

    text = format_dual_horizon_console(df)

    assert "단기:" in text
    assert "중기:" in text
    assert df.loc[0, "horizon_label"] in text


def test_dual_horizon_risky_leader_becomes_chase_risk_not_risk_only():
    df = add_dual_horizon_fields(pd.DataFrame([{
        "code": "000401",
        "name": "위험주도",
        "primary_bucket": "TIER_3",
        "watch_alert_type": "RISK_WATCH",
        "change_pct": 0.035,
        "trading_value": 90_000_000_000,
        "breakout_status": "NEAR_BREAKOUT",
        "breakout_distance_pct": 2.0,
        "rs_rating": 93,
        "vcp_status": "REVERSE_EXPANSION",
        "market_cap": 8_000_000_000_000,
        "liquidity_status": "LIQUID",
        "score_max": 210,
    }]))

    assert df.loc[0, "horizon_label"] == "CHASE_RISK"
    assert df.loc[0, "short_swing_score"] > 0


def test_dual_horizon_failed_breakout_with_risk_is_not_short_swing():
    df = add_dual_horizon_fields(pd.DataFrame([{
        "code": "000402",
        "name": "돌파실패",
        "primary_bucket": "TIER_3",
        "watch_alert_type": "ACTION_ALERT",
        "change_pct": 0.02,
        "trading_value": 70_000_000_000,
        "breakout_status": "FAILED_BREAKOUT",
        "breakout_distance_pct": 0.2,
        "breakout_volume_ratio": 0.6,
        "rs_rating": 91,
        "vcp_status": "RALLY_EXHAUSTION",
        "market_cap": 6_000_000_000_000,
        "liquidity_status": "LIQUID",
        "score_max": 210,
    }]))

    assert df.loc[0, "horizon_label"] == "CHASE_RISK"
    assert df.loc[0, "horizon_label"] != "SHORT_SWING"
