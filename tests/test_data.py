"""
data 레이어 단위 테스트.
실제 pykrx 호출 없이 mock으로 검증합니다.

실행:
    pytest tests/test_data.py -v
"""
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.holidays import (
    is_trading_day,
    latest_trading_day,
    prev_trading_day,
    next_trading_day,
    to_krx_date,
    trading_days_between,
)
from data.cache import DataCache


# ── 휴장일 테스트 ─────────────────────────────────────────────────────────────

class TestHolidays:
    def test_saturday_not_trading(self):
        assert is_trading_day("2026-05-02") is False   # 토요일

    def test_sunday_not_trading(self):
        assert is_trading_day("2026-05-03") is False   # 일요일

    def test_childrens_day_not_trading(self):
        assert is_trading_day("2026-05-05") is False   # 어린이날

    def test_regular_weekday_is_trading(self):
        assert is_trading_day("2026-05-04") is True    # 월요일, 공휴일 아님

    def test_prev_trading_day_skips_weekend(self):
        # 2026-05-04(월)의 이전 거래일은 2026-04-30(목) — 주말+어린이날 건너뜀
        result = prev_trading_day("2026-05-04", n=1)
        # 2026-05-01(금)은 거래일
        assert result == "2026-05-01"

    def test_prev_trading_day_skips_holiday(self):
        # 2026-05-06(수)의 이전 거래일: 05-05(화)는 어린이날 → 05-04(월)
        result = prev_trading_day("2026-05-06", n=1)
        assert result == "2026-05-04"

    def test_next_trading_day(self):
        # 2026-05-01(금) 다음 거래일: 05-02(토), 05-03(일), 05-04(월)
        result = next_trading_day("2026-05-01", n=1)
        assert result == "2026-05-04"

    def test_to_krx_date(self):
        assert to_krx_date("2026-05-05") == "20260505"
        assert to_krx_date(date(2026, 5, 5)) == "20260505"

    def test_trading_days_between(self):
        days = trading_days_between("2026-04-27", "2026-05-04")
        # 04-27(월), 04-28(화), 04-29(수), 04-30(목), 05-01(금), 05-04(월)
        assert "2026-04-27" in days
        assert "2026-05-02" not in days   # 토요일
        assert "2026-05-03" not in days   # 일요일
        assert "2026-05-04" in days

    def test_trading_days_count(self):
        days = trading_days_between("2026-04-27", "2026-05-04")
        assert len(days) == 6

    def test_latest_trading_day_returns_string(self):
        result = latest_trading_day("2026-05-05")
        assert isinstance(result, str)
        assert len(result) == 10   # YYYY-MM-DD


# ── 캐시 테스트 ───────────────────────────────────────────────────────────────

class TestDataCache:
    @pytest.fixture
    def cache(self, tmp_path):
        return DataCache(tmp_path)

    @pytest.fixture
    def sample_df(self):
        return pd.DataFrame({
            "code":   ["005930", "000660"],
            "name":   ["삼성전자", "SK하이닉스"],
            "close":  [70000.0, 150000.0],
            "volume": [10000000, 5000000],
        })

    def test_make_key_format(self, cache):
        key = cache.make_key("universe", "2026-05-05", "all")
        assert key == "universe_20260505_all"

    def test_make_key_removes_dashes(self, cache):
        key = cache.make_key("ohlcv", "005930", "2026-04-01", "2026-05-05")
        assert "-" not in key

    def test_save_and_load(self, cache, sample_df):
        key = "test_save_load"
        cache.save(key, sample_df)
        loaded = cache.load(key)

        assert loaded is not None
        assert len(loaded) == 2
        assert list(loaded["code"]) == ["005930", "000660"]

    def test_load_nonexistent_returns_none(self, cache):
        assert cache.load("nonexistent_key") is None

    def test_exists(self, cache, sample_df):
        key = "test_exists"
        assert cache.exists(key) is False
        cache.save(key, sample_df)
        assert cache.exists(key) is True

    def test_delete(self, cache, sample_df):
        key = "test_delete"
        cache.save(key, sample_df)
        assert cache.exists(key) is True
        cache.delete(key)
        assert cache.exists(key) is False

    def test_empty_df_not_saved(self, cache):
        key = "test_empty"
        cache.save(key, pd.DataFrame())
        assert cache.exists(key) is False

    def test_load_or_fetch_uses_cache(self, cache, sample_df):
        key = "test_fetch"
        cache.save(key, sample_df)

        call_count = {"n": 0}
        def fetch_fn():
            call_count["n"] += 1
            return pd.DataFrame()

        result = cache.load_or_fetch(key, fetch_fn)
        assert call_count["n"] == 0    # fetch 호출 안 됨
        assert len(result) == 2

    def test_load_or_fetch_calls_fn_when_miss(self, cache, sample_df):
        key = "test_miss"

        def fetch_fn():
            return sample_df

        result = cache.load_or_fetch(key, fetch_fn)
        assert len(result) == 2
        assert cache.exists(key) is True   # 저장됐는지 확인

    def test_force_refresh(self, cache, sample_df):
        key = "test_force"
        cache.save(key, sample_df)

        new_df = pd.DataFrame({"code": ["000020"], "name": ["동화약품"]})

        def fetch_fn():
            return new_df

        result = cache.load_or_fetch(key, fetch_fn, force_refresh=True)
        assert list(result["code"]) == ["000020"]   # 새 데이터로 교체됨

    def test_list_keys(self, cache, sample_df):
        cache.save("alpha", sample_df)
        cache.save("beta",  sample_df)
        keys = cache.list_keys()
        assert "alpha" in keys
        assert "beta"  in keys

    def test_clear_all(self, cache, sample_df):
        cache.save("a", sample_df)
        cache.save("b", sample_df)
        count = cache.clear_all()
        assert count == 2
        assert cache.list_keys() == []
