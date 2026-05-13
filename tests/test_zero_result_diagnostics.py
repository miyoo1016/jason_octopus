from types import SimpleNamespace

import pandas as pd

from backend.analysis_summary import build_analysis_payload
from engine.node_base import ExecutionContext
from nodes.box_breakout import BoxBreakoutNode, BoxBreakoutParams
from nodes.foreign_flow import ForeignFlowNode, ForeignFlowParams
from nodes.institution_flow import InstitutionFlowNode, InstitutionFlowParams
from nodes.macro_filter import MacroFilterNode, MacroFilterParams
from nodes.rs_rating import RsRatingNode, RsRatingParams
from nodes.score_filter import ScoreFilterNode, ScoreFilterParams
from nodes.vcp import VcpNode, VcpParams


def _universe():
    return pd.DataFrame([
        {"code": "000001", "name": "A", "market": "KOSPI", "close": 1000, "volume": 100000, "market_cap": 100_000_000_000},
        {"code": "000002", "name": "B", "market": "KOSPI", "close": 2000, "volume": 200000, "market_cap": 200_000_000_000},
    ])


def _mock_universe(n=30):
    return pd.DataFrame([
        {
            "code": f"{i:06d}",
            "name": f"S{i}",
            "market": "KOSPI",
            "close": 1000 + i,
            "volume": 100000 + i,
            "market_cap": 100_000_000_000 + i,
        }
        for i in range(1, n + 1)
    ])


class _BoxClient:
    def __init__(self, mode="not_ready"):
        self.mode = mode

    def get_ohlcv_batch(self, codes, start_date="", end_date="", pages=3, **kw):
        dates = pd.date_range("2026-01-01", periods=90, freq="B")
        out = {}
        for idx, code in enumerate(codes):
            close = [100.0] * len(dates)
            high = [120.0] * (len(dates) - 1) + [101.0]
            low = [95.0] * len(dates)
            volume = [10000.0] * len(dates)
            if self.mode == "confirmed" and idx == 0:
                close[-1] = 123.0
                high[-1] = 124.0
                volume[-1] = 30000.0
            out[code] = pd.DataFrame({
                "open": [99.0] * len(dates),
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }, index=dates)
        return out


def test_vcp_data_missing_keeps_rows_and_score_flag():
    class FakeClient:
        def get_ohlcv_batch(self, codes, start_date="", end_date="", pages=3, **kw):
            return {}

    ctx = ExecutionContext(as_of_date="2026-05-07", run_id="t", krx_client=FakeClient())
    out = VcpNode().run([_universe()], VcpParams(), ctx)

    assert len(out) == 2
    assert set(out["vcp_flag"]) == {"DATA_MISSING"}
    # [변경] DATA_MISSING은 판단 보류 — 50점 가산 금지, None으로 처리
    assert all(v is None or pd.isna(v) for v in out["vcp_score"])


def test_vcp_reverse_expansion_is_not_strict():
    class FakeClient:
        def get_ohlcv_batch(self, codes, start_date="", end_date="", pages=3, **kw):
            dates = pd.date_range("2026-01-01", periods=120, freq="B")
            high = [100.0] * len(dates)
            low = [96.0] * len(dates)
            close = [98.0] * len(dates)
            for idx, h, l in [(20, 110, 104), (40, 108, 96), (60, 106, 85), (80, 104, 82)]:
                high[idx] = h
                close[idx] = h - 1
                low[idx] = l
            close[-1] = 100
            return {
                code: pd.DataFrame({
                    "open": close,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": [10000.0] * len(dates),
                }, index=dates)
                for code in codes
            }

    ctx = ExecutionContext(as_of_date="2026-06-17", run_id="t", krx_client=FakeClient())
    out = VcpNode().run([_universe().head(1)], VcpParams(), ctx)

    assert out.loc[0, "vcp_status"] != "VCP_STRICT"
    assert out.loc[0, "vcp_status"] in {"REVERSE_EXPANSION", "VCP_WARNING"}


def test_rs_data_missing_keeps_rows_with_neutral_score():
    class FakeClient:
        def get_universe(self, as_of_date):
            return _universe()

        def get_ohlcv_batch(self, codes, start_date="", end_date="", pages=3, **kw):
            return {}

    ctx = ExecutionContext(as_of_date="2026-05-07", run_id="t", krx_client=FakeClient())
    out = RsRatingNode().run([_universe()], RsRatingParams(), ctx)

    assert len(out) == 2
    assert set(out["rs_flag"]) == {"DATA_MISSING"}
    # [변경] DATA_MISSING은 판단 보류 — 50점 가산 금지, None으로 처리
    assert all(v is None or pd.isna(v) for v in out["rs_score"])


def test_flow_data_missing_keeps_rows_with_data_missing_flags():
    class FakeClient:
        def get_foreign_flow(self, df, as_of_date, n_days=5):
            out = df.copy()
            out["foreign_net_buy"] = pd.Series([pd.NA, pd.NA], dtype="Int64")
            out.attrs["foreign_flow_hist"] = {}
            return out

        def get_institution_flow(self, df, as_of_date, n_days=5):
            out = df.copy()
            out["institution_net_buy"] = pd.Series([pd.NA, pd.NA], dtype="Int64")
            out.attrs["institution_flow_hist"] = {}
            return out

    ctx = ExecutionContext(as_of_date="2026-05-07", run_id="t", krx_client=FakeClient())
    f = ForeignFlowNode().run([_universe()], ForeignFlowParams(), ctx)
    i = InstitutionFlowNode().run([f], InstitutionFlowParams(), ctx)

    assert len(i) == 2
    assert set(i["foreign_flow_flag"]) == {"DATA_MISSING"}
    assert set(i["institution_flow_flag"]) == {"DATA_MISSING"}
    assert set(i["flow_score"]) == {50}
    assert set(i["institution_flow_score"]) == {50}


def test_macro_fetch_failure_keeps_rows_with_unknown_status(monkeypatch):
    def fail_download(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("nodes.macro_filter.yf.download", fail_download)
    
    class MockTicker:
        def __init__(self, *args, **kwargs): pass
        def history(self, *args, **kwargs): raise RuntimeError("network down")
        
    monkeypatch.setattr("nodes.macro_filter.yf.Ticker", MockTicker)
    monkeypatch.setattr("nodes.macro_filter._load_macro_cache", lambda: None)
    ctx = ExecutionContext(as_of_date="2026-05-07", run_id="t", krx_client=None)

    out = MacroFilterNode().run([_universe()], MacroFilterParams(), ctx)

    assert len(out) == 2
    assert set(out["macro_flag"]) == {"DATA_MISSING"}
    assert set(out["macro_score"]) == {50}


def test_box_breakout_keeps_all_rows_when_no_breakout():
    df = _mock_universe(30)
    ctx = ExecutionContext(as_of_date="2026-05-07", run_id="t", krx_client=_BoxClient())
    out = BoxBreakoutNode().run([df], BoxBreakoutParams(box_period=60), ctx)

    assert len(out) == 30
    assert "breakout_status" in out.columns
    assert "breakout_score" in out.columns
    assert out["box_high"].notna().all()
    assert set(out["breakout_status"]) == {"NOT_READY"}
    assert out["breakout_score"].notna().all()


def test_box_breakout_preserves_non_confirmed_rows_with_statuses():
    df = _mock_universe(30)
    ctx = ExecutionContext(as_of_date="2026-05-07", run_id="t", krx_client=_BoxClient(mode="confirmed"))
    out = BoxBreakoutNode().run([df], BoxBreakoutParams(box_period=60), ctx)

    assert len(out) == 30
    assert "BREAKOUT_CONFIRMED" in set(out["breakout_status"])
    assert len(out[out["breakout_status"] != "BREAKOUT_CONFIRMED"]) == 29
    assert out["breakout_score"].notna().all()


def test_score_filter_returns_fallback_watchlist_candidates_when_no_tier12():
    df = _universe()
    df["vcp_score"] = [50, 40]
    df["vcp_status"] = ["BASE_BUILDING", "NOT_READY"]
    df["rs_score"] = [50, 50]
    df["rs_rating"] = [None, None]
    df["flow_score"] = [50, 50]
    df["institution_flow_score"] = [50, 50]
    df["foreign_net_buy"] = pd.Series([pd.NA, pd.NA], dtype="Int64")
    df["institution_net_buy"] = pd.Series([pd.NA, pd.NA], dtype="Int64")

    scored = ScoreFilterNode().run([df], ScoreFilterParams(), ExecutionContext(as_of_date="2026-05-07", run_id="t"))
    logs = [
        SimpleNamespace(node_id="n1", node_type="universe", input_count=0, output_count=2, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[], drop_reasons=[]),
        SimpleNamespace(node_id="n2", node_type="score_filter", input_count=2, output_count=2, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=2, data_missing_ratio=1, nan_columns=[], drop_reasons=[]),
    ]
    result = SimpleNamespace(outputs={"n1": _universe(), "n2": scored}, node_logs=logs)
    payload = build_analysis_payload(result, {})

    assert payload["summary"]["tier1_count"] == 0
    assert payload["summary"]["tier2_count"] == 0
    assert payload["results"]["fallback_candidates"]["top_score_candidates"]
    assert payload["diagnostics"]["node_counts"][1]["output_count"] == 2


def test_score_filter_blocks_weak_breakout_from_tier1():
    df = _universe().head(1)
    df["vcp_score"] = [95]
    df["vcp_status"] = ["VCP_STRICT"]
    df["vcp_warning"] = ["VCP_STRICT"]
    df["breakout_score"] = [5]
    df["breakout_status"] = ["NOT_READY"]
    df["box_breakout_warning"] = ["거래량 부족"]
    df["rs_score"] = [50]
    df["rs_rating"] = [92]
    df["rs_status"] = ["Strong"]
    df["flow_score"] = [30]
    df["institution_flow_score"] = [30]
    df["foreign_net_buy"] = [100000]
    df["institution_net_buy"] = [100000]
    df["ma_alignment_flag"] = ["ALIGNED"]
    df["liquidity_status"] = ["LIQUID"]

    out = ScoreFilterNode().run([df], ScoreFilterParams(), ExecutionContext(as_of_date="2026-05-07", run_id="t"))

    assert out.loc[0, "primary_bucket"] != "TIER_1"
    assert "돌파 NOT_READY" in out.loc[0, "downgrade_reasons"]


def test_score_filter_blocks_rs_50s_from_tier1():
    df = _universe().head(1)
    df["vcp_score"] = [95]
    df["vcp_status"] = ["VCP_STRICT"]
    df["vcp_warning"] = ["VCP_STRICT"]
    df["breakout_score"] = [30]
    df["breakout_status"] = ["BREAKOUT_CONFIRMED"]
    df["rs_score"] = [20]
    df["rs_rating"] = [55]
    df["rs_status"] = ["LOW_RS"]
    df["flow_score"] = [30]
    df["institution_flow_score"] = [30]
    df["foreign_net_buy"] = [100000]
    df["institution_net_buy"] = [100000]
    df["ma_alignment_flag"] = ["ALIGNED"]
    df["liquidity_status"] = ["LIQUID"]

    out = ScoreFilterNode().run([df], ScoreFilterParams(), ExecutionContext(as_of_date="2026-05-07", run_id="t"))

    assert out.loc[0, "primary_bucket"] != "TIER_1"
    assert "RS 80 미달" in out.loc[0, "downgrade_reasons"]


def test_primary_bucket_counts_are_exclusive_and_watchlist_flag_separate():
    df = _universe()
    df["total_score"] = [180, 60]
    df["final_score"] = [180, 60]
    df["tier"] = [1, None]
    df["primary_bucket"] = ["TIER_1", "REJECTED"]
    df["candidate_status"] = ["TIER_1", "REJECTED"]
    df["watchlist_flag"] = [False, False]
    logs = [
        SimpleNamespace(node_id="n1", node_type="universe", input_count=0, output_count=2, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[], drop_reasons=[]),
        SimpleNamespace(node_id="n2", node_type="score_filter", input_count=2, output_count=2, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[], drop_reasons=[]),
    ]
    payload = build_analysis_payload(SimpleNamespace(outputs={"n1": _universe(), "n2": df}, node_logs=logs), {})

    assert payload["summary"]["primary_count_total"] == payload["summary"]["universe_count"]
    assert payload["summary"]["primary_counts"] == {"TIER_1": 1, "TIER_2": 0, "TIER_3": 0, "WATCHLIST": 0, "CRISIS_HOLD": 0, "REJECTED": 1}
    assert payload["summary"]["watchlist_flag_count"] == 0


def test_score_filter_rejects_clearly_weak_candidate():
    df = _universe().head(1)
    df["vcp_score"] = [20]
    df["vcp_status"] = ["NOT_READY"]
    df["vcp_warning"] = ["거래량 미감소"]
    df["breakout_score"] = [5]
    df["breakout_status"] = ["NOT_READY"]
    df["rs_score"] = [10]
    df["rs_rating"] = [35]
    df["rs_status"] = ["LOW_RS"]
    df["flow_score"] = [0]
    df["institution_flow_score"] = [0]
    df["foreign_net_buy"] = [-10000]
    df["institution_net_buy"] = [-10000]
    df["ma_alignment_flag"] = ["NOT_ALIGNED"]
    df["liquidity_status"] = ["LOW_LIQUIDITY"]

    out = ScoreFilterNode().run([df], ScoreFilterParams(), ExecutionContext(as_of_date="2026-05-07", run_id="t"))

    assert out.loc[0, "primary_bucket"] == "REJECTED"
    assert out.loc[0, "watchlist_flag"] == False


def test_filtered_count_captures_rows_lost_before_final_scoring():
    scored = _universe().head(1).copy()
    scored["total_score"] = [50]
    scored["final_score"] = [50]
    scored["tier"] = [3]
    scored["candidate_status"] = ["WATCHLIST"]
    logs = [
        SimpleNamespace(node_id="n1", node_type="universe", input_count=0, output_count=2, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[], drop_reasons=[]),
        SimpleNamespace(node_id="n2", node_type="box_breakout", input_count=2, output_count=1, dropped_count=1, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[], drop_reasons=[]),
        SimpleNamespace(node_id="n3", node_type="score_filter", input_count=1, output_count=1, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[], drop_reasons=[]),
    ]
    result = SimpleNamespace(outputs={"n1": _universe(), "n2": scored, "n3": scored}, node_logs=logs)
    payload = build_analysis_payload(result, {})

    assert payload["summary"]["filtered_count"] == 1
    assert payload["summary"]["classified_count"] == 1
    assert payload["diagnostics"]["most_aggressive_filter_node"]["node_type"] == "box_breakout"


def test_warning_columns_are_not_reported_as_nan_diagnostics():
    scored = _universe()
    scored["total_score"] = [50, 40]
    scored["final_score"] = [50, 40]
    scored["tier"] = [3, 3]
    scored["candidate_status"] = ["WATCHLIST", "WATCHLIST"]
    scored["institution_flow_warning"] = [None, None]
    scored["macro_warning"] = [None, None]
    scored["rs_score"] = [50, 50]
    logs = [
        SimpleNamespace(node_id="n1", node_type="universe", input_count=0, output_count=2, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[
            {"column": "institution_flow_warning", "nan_count": 2},
            {"column": "rs_score", "nan_count": 0},
        ], drop_reasons=[]),
        SimpleNamespace(node_id="n2", node_type="score_filter", input_count=2, output_count=2, dropped_count=0, latency_ms=1, cache_hit=False, data_missing_count=0, data_missing_ratio=0, nan_columns=[], drop_reasons=[]),
    ]
    result = SimpleNamespace(outputs={"n1": _universe(), "n2": scored}, node_logs=logs)
    payload = build_analysis_payload(result, {})

    assert all(item["column"] != "institution_flow_warning" for item in payload["diagnostics"]["nan_columns"])
    assert all(
        item["column"] != "institution_flow_warning"
        for node in payload["diagnostics"]["node_counts"]
        for item in node["nan_columns"]
    )


def test_frontend_reads_structured_results_and_diagnostics():
    js = open("frontend/strategy-builder.js", encoding="utf-8").read()
    assert "collectStructuredRows" in js
    assert "renderDiagnosticsPanel" in js
    assert "fallback_candidates" in js
    assert "result.summary?.universe_count" in js
