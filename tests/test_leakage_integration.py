"""
Look-ahead bias 통합 테스트.

미래 데이터를 흘려넣어주는 mock KRX client를 만들고,
실제 노드/백테스트가 LeakageError를 일으키는지 확인합니다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestEngine, BacktestParams
from engine.dag import LeakageError
from engine.node_base import ExecutionContext
from nodes import BoxBreakoutNode, MaAlignmentNode, VcpNode


class _LeakyKrxClient:
    """
    의도적으로 as_of_date 이후 데이터까지 반환하는 악성 mock.
    진짜 네이버 API의 'pageSize 무시 + 항상 최근' 동작을 재현합니다.
    """

    def get_ohlcv_batch(self, codes, start_date="", end_date="", **kw):
        result = {}
        # as_of_date를 무시하고 항상 미래까지 포함된 데이터를 반환
        dates = pd.date_range("2026-01-01", "2026-12-31", freq="B")
        for code in codes:
            df = pd.DataFrame({
                "open":   [100] * len(dates),
                "high":   [110] * len(dates),
                "low":    [90]  * len(dates),
                "close":  [105] * len(dates),
                "volume": [10000] * len(dates),
            }, index=dates)
            df.index.name = "date"
            result[code] = df
        return result


class _SafeKrxClient:
    """as_of_date 이전 데이터만 반환하는 정상 mock."""

    def get_ohlcv_batch(self, codes, start_date="", end_date="", **kw):
        result = {}
        end_ts = pd.Timestamp(end_date) if end_date else pd.Timestamp("2026-12-31")
        start_ts = pd.Timestamp(start_date) if start_date else pd.Timestamp("2025-01-01")
        dates = pd.date_range(start_ts, end_ts, freq="B")
        for code in codes:
            df = pd.DataFrame({
                "open":   [100] * len(dates),
                "high":   [110] * len(dates),
                "low":    [90]  * len(dates),
                "close":  [105] * len(dates),
                "volume": [10000] * len(dates),
            }, index=dates)
            df.index.name = "date"
            result[code] = df
        return result


def _make_ctx(as_of: str, krx) -> ExecutionContext:
    return ExecutionContext(
        as_of_date=as_of,
        run_id="test_run",
        cache_dir="/tmp",
        krx_client=krx,
        extras={},
    )


def _signal_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "close": 100, "volume": 1000},
    ])


# ── 노드 단위 ───────────────────────────────────────────────────────────────

class TestNodeLeakage:
    def test_vcp_rejects_future_data(self):
        node = VcpNode()
        params = node.validate_params({"lookback_days": 60})
        ctx = _make_ctx("2026-03-02", _LeakyKrxClient())
        with pytest.raises(LeakageError, match="VcpNode"):
            node.run([_signal_df()], params, ctx)

    def test_box_breakout_rejects_future_data(self):
        node = BoxBreakoutNode()
        params = node.validate_params({"box_period": 30})
        ctx = _make_ctx("2026-03-02", _LeakyKrxClient())
        with pytest.raises(LeakageError, match="BoxBreakoutNode"):
            node.run([_signal_df()], params, ctx)

    def test_ma_alignment_rejects_future_data(self):
        node = MaAlignmentNode()
        params = node.validate_params({})
        ctx = _make_ctx("2026-03-02", _LeakyKrxClient())
        with pytest.raises(LeakageError, match="MaAlignmentNode"):
            node.run([_signal_df()], params, ctx)

    def test_vcp_passes_with_safe_data(self):
        """정상 클라이언트로는 LeakageError가 발생하지 않아야 함."""
        node = VcpNode()
        params = node.validate_params({"lookback_days": 60})
        ctx = _make_ctx("2026-03-02", _SafeKrxClient())
        # 결과는 빈 DF일 수 있으나 예외는 없어야 함
        result = node.run([_signal_df()], params, ctx)
        assert isinstance(result, pd.DataFrame)


# ── BacktestEngine ──────────────────────────────────────────────────────────

class TestBacktestLeakage:
    def test_backtest_rejects_signal_date_leak(self):
        """신호일 당일 데이터로 진입가를 계산하면 LeakageError."""

        class _BadBacktestClient:
            def get_ohlcv_batch(self, codes, start_date="", end_date="", **kw):
                # signal_date(2026-03-02 월요일) 당일 데이터 포함 = 누출
                dates = pd.date_range("2026-03-02", "2026-04-01", freq="B")
                return {
                    code: pd.DataFrame({
                        "open":   [100] * len(dates),
                        "high":   [110] * len(dates),
                        "low":    [90]  * len(dates),
                        "close":  [105] * len(dates),
                        "volume": [10000] * len(dates),
                    }, index=dates)
                    for code in codes
                }

        engine = BacktestEngine(_BadBacktestClient())
        signal_df = pd.DataFrame([{"code": "005930", "name": "삼성전자"}])
        with pytest.raises(LeakageError, match="신호일"):
            engine.run_signal_backtest(signal_df, "2026-03-02", BacktestParams(hold_days=10))

    def test_backtest_passes_with_strictly_future_data(self):
        """signal_date 다음 거래일 이후 데이터만 있으면 정상."""

        class _GoodBacktestClient:
            def get_ohlcv_batch(self, codes, start_date="", end_date="", **kw):
                # 2026-03-03 부터 시작 (signal_date 2026-03-02 초과)
                dates = pd.date_range("2026-03-03", "2026-04-01", freq="B")
                return {
                    code: pd.DataFrame({
                        "open":   [100] * len(dates),
                        "high":   [110] * len(dates),
                        "low":    [90]  * len(dates),
                        "close":  [105] * len(dates),
                        "volume": [10000] * len(dates),
                    }, index=dates)
                    for code in codes
                }

        engine = BacktestEngine(_GoodBacktestClient())
        signal_df = pd.DataFrame([{"code": "005930", "name": "삼성전자"}])
        result = engine.run_signal_backtest(signal_df, "2026-03-02", BacktestParams(hold_days=10))
        assert result["trade_count"] >= 0  # 정상 실행, 예외 없음
