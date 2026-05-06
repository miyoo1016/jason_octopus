"""
Parquet 기반 로컬 캐시.

T-1(전일) 이전 데이터는 확정값이므로 영구 캐시합니다.
당일 데이터는 캐시하지 않습니다 (pykrx가 T-1 기준이므로 사실상 해당 없음).

파일 구조:
    data/cache/
    ├── universe_20260505.parquet
    ├── ohlcv_005930_20260430_20260505.parquet
    ├── foreign_flow_20260505.parquet
    └── institution_flow_20260505.parquet

사용법:
    from data.cache import DataCache
    from backend.config import settings

    cache = DataCache(settings.data_cache_dir)
    df = cache.load("universe", "2026-05-05")
    if df is None:
        df = fetch_from_pykrx(...)
        cache.save("universe", "2026-05-05", df)
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class DataCache:
    """Parquet 파일 기반 데이터 캐시."""

    def __init__(self, cache_dir: str | Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _key_to_path(self, key: str) -> Path:
        """캐시 키를 파일 경로로 변환합니다."""
        # 파일명에 쓸 수 없는 문자 제거
        safe_key = key.replace("/", "_").replace(":", "_").replace(" ", "_")
        return self._dir / f"{safe_key}.parquet"

    @staticmethod
    def make_key(data_type: str, *parts: str) -> str:
        """캐시 키를 생성합니다.

        Examples:
            make_key("universe", "2026-05-05")
            → "universe_20260505"

            make_key("ohlcv", "005930", "2026-04-01", "2026-05-05")
            → "ohlcv_005930_20260401_20260505"
        """
        date_parts = [p.replace("-", "") for p in parts]
        return "_".join([data_type] + date_parts)

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def exists(self, key: str) -> bool:
        """캐시 파일이 존재하는지 확인합니다."""
        return self._key_to_path(key).exists()

    def load(self, key: str) -> pd.DataFrame | None:
        """
        캐시를 로드합니다. 없으면 None 반환.

        Args:
            key: make_key()로 생성한 캐시 키

        Returns:
            DataFrame 또는 None
        """
        path = self._key_to_path(key)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            logger.debug("캐시 히트: %s (%d행)", key, len(df))
            return df
        except Exception as exc:
            logger.warning("캐시 로드 실패 (%s): %s — 캐시를 무시합니다.", key, exc)
            path.unlink(missing_ok=True)   # 깨진 캐시 삭제
            return None

    def save(self, key: str, df: pd.DataFrame) -> None:
        """
        DataFrame을 Parquet으로 저장합니다.

        Args:
            key: make_key()로 생성한 캐시 키
            df:  저장할 DataFrame
        """
        if df is None or df.empty:
            logger.debug("빈 DataFrame은 캐시하지 않습니다: %s", key)
            return
        path = self._key_to_path(key)
        try:
            df.to_parquet(path, index=True, compression="snappy")
            logger.debug("캐시 저장: %s (%d행, %.1fKB)", key, len(df), path.stat().st_size / 1024)
        except Exception as exc:
            logger.error("캐시 저장 실패 (%s): %s", key, exc)
            path.unlink(missing_ok=True)

    def load_or_fetch(
        self,
        key: str,
        fetch_fn,          # Callable[[], pd.DataFrame]
        *,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        캐시가 있으면 로드, 없으면 fetch_fn을 호출해 저장 후 반환합니다.

        Args:
            key:           캐시 키
            fetch_fn:      인자 없는 callable, DataFrame 반환
            force_refresh: True이면 캐시를 무시하고 강제로 재수집

        Returns:
            DataFrame
        """
        if not force_refresh:
            cached = self.load(key)
            if cached is not None:
                return cached

        logger.info("데이터 수집 시작: %s", key)
        df = fetch_fn()
        self.save(key, df)
        return df

    def delete(self, key: str) -> bool:
        """캐시 파일을 삭제합니다. 삭제 성공 여부 반환."""
        path = self._key_to_path(key)
        if path.exists():
            path.unlink()
            logger.info("캐시 삭제: %s", key)
            return True
        return False

    def clear_all(self) -> int:
        """캐시 디렉터리의 모든 Parquet 파일을 삭제합니다. 삭제 수 반환."""
        count = 0
        for f in self._dir.glob("*.parquet"):
            f.unlink()
            count += 1
        logger.info("캐시 전체 삭제: %d개 파일", count)
        return count

    def list_keys(self) -> list[str]:
        """저장된 캐시 키 목록을 반환합니다."""
        return sorted(
            f.stem for f in self._dir.glob("*.parquet")
        )

    def total_size_mb(self) -> float:
        """캐시 디렉터리 총 크기(MB)를 반환합니다."""
        total = sum(f.stat().st_size for f in self._dir.glob("*.parquet"))
        return round(total / (1024 * 1024), 2)
