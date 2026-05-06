"""
Look-ahead bias 방어선 테스트.

검증 항목:
  - assert_no_future_data: as_of_date 이후 데이터 존재 시 LeakageError
  - filter_to_as_of: 경계 외 데이터 필터링 정확성
  - assert_after: signal_date 이하 데이터 존재 시 LeakageError
  - 노드(VCP/MA/BoxBreakout)가 미래 데이터 받으면 LeakageError 발생
  - BacktestEngine이 signal_date 이하 데이터를 진입에 쓰면 LeakageError
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.dag import LeakageError
from engine.leakage_guard import (
    assert_after,
    assert_no_future_data,
    filter_to_as_of,
)


def _make_ohlcv(dates: list[str]) -> pd.DataFrame:
    """주어진 날짜로 더미 OHLCV DataFrame 생성 (DatetimeIndex)."""
    df = pd.DataFrame({
        "open":   [100] * len(dates),
        "high":   [110] * len(dates),
        "low":    [90]  * len(dates),
        "close":  [105] * len(dates),
        "volume": [1000] * len(dates),
    }, index=pd.to_datetime(dates))
    df.index.name = "date"
    return df


# ── assert_no_future_data ───────────────────────────────────────────────────

class TestAssertNoFutureData:
    def test_safe_data_passes(self):
        df = _make_ohlcv(["2026-01-01", "2026-01-15", "2026-02-01"])
        assert_no_future_data(df, "2026-02-01")

    def test_boundary_inclusive(self):
        """as_of_date 당일 데이터는 통과(<=)."""
        df = _make_ohlcv(["2026-02-01"])
        assert_no_future_data(df, "2026-02-01")

    def test_future_data_raises(self):
        df = _make_ohlcv(["2026-01-01", "2026-03-15"])
        with pytest.raises(LeakageError, match="누출"):
            assert_no_future_data(df, "2026-02-01")

    def test_empty_df_passes(self):
        df = _make_ohlcv([])
        assert_no_future_data(df, "2026-02-01")

    def test_none_passes(self):
        assert_no_future_data(None, "2026-02-01")

    def test_with_date_column(self):
        df = pd.DataFrame({
            "code":  ["A", "B", "C"],
            "date":  ["2026-01-01", "2026-01-15", "2026-03-01"],
            "value": [1, 2, 3],
        })
        with pytest.raises(LeakageError):
            assert_no_future_data(df, "2026-02-01", date_col="date")

    def test_non_datetime_index_skipped(self):
        """DatetimeIndex가 아니고 date_col도 없으면 검증 생략."""
        df = pd.DataFrame({"x": [1, 2, 3]})
        assert_no_future_data(df, "2026-02-01")  # 예외 없음


# ── filter_to_as_of ─────────────────────────────────────────────────────────

class TestFilterToAsOf:
    def test_filter_index(self):
        df = _make_ohlcv(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"])
        out = filter_to_as_of(df, "2026-02-15")
        assert len(out) == 2
        assert out.index.max() == pd.Timestamp("2026-02-01")

    def test_filter_date_column(self):
        df = pd.DataFrame({
            "code": ["A", "B", "C"],
            "date": ["2026-01-01", "2026-02-15", "2026-03-01"],
        })
        out = filter_to_as_of(df, "2026-02-15", date_col="date")
        assert len(out) == 2  # 경계 포함

    def test_empty_df(self):
        df = _make_ohlcv([])
        out = filter_to_as_of(df, "2026-02-01")
        assert out.empty


# ── assert_after ────────────────────────────────────────────────────────────

class TestAssertAfter:
    def test_strictly_after_passes(self):
        df = _make_ohlcv(["2026-02-02", "2026-02-05"])
        assert_after(df, "2026-02-01")

    def test_boundary_raises(self):
        """signal_date 당일 데이터가 진입 후보에 있으면 누출 (<=)."""
        df = _make_ohlcv(["2026-02-01", "2026-02-05"])
        with pytest.raises(LeakageError, match="신호일"):
            assert_after(df, "2026-02-01")

    def test_before_raises(self):
        df = _make_ohlcv(["2026-01-15", "2026-02-05"])
        with pytest.raises(LeakageError):
            assert_after(df, "2026-02-01")

    def test_empty_passes(self):
        df = _make_ohlcv([])
        assert_after(df, "2026-02-01")
