"""
DAG 노드 실행 결과 캐시 (Parquet 기반).

data/cache.py가 외부 API에서 받은 원시 데이터(유니버스, OHLCV 등)를 캐시한다면,
이 모듈은 DAG 중간 노드의 실행 결과를 캐시합니다.

캐시 키는 DAG 엔진이 생성한 SHA256 해시(64자 hex)를 그대로 사용합니다.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class ResultCache:
    """노드 실행 결과 Parquet 캐시.

    캐시 키는 DAG._compute_cache_key()가 생성합니다.
    동일한 키 → 동일한 결과를 보장합니다 (결정성).
    """

    def __init__(self, cache_dir: str | Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def cache_dir(self) -> Path:
        return self._dir

    def _path(self, cache_key: str) -> Path:
        return self._dir / f"{cache_key}.parquet"

    def has(self, cache_key: str) -> bool:
        return self._path(cache_key).exists()

    def get(self, cache_key: str) -> pd.DataFrame | None:
        """캐시 히트 시 DataFrame 반환, 미스 시 None."""
        path = self._path(cache_key)
        if not path.exists():
            return None
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            logger.warning("결과 캐시 로드 실패 (%s): %s — 캐시 무시", cache_key[:16], exc)
            path.unlink(missing_ok=True)
            return None

    def put(self, cache_key: str, df: pd.DataFrame) -> None:
        """DataFrame을 Parquet으로 저장. 빈 DataFrame은 저장 생략."""
        if df is None or df.empty:
            logger.debug("빈 DataFrame은 캐시하지 않음: %s", cache_key[:16])
            return
        try:
            # df.attrs에 Timestamp 등 비직렬화 객체가 있으면 parquet 메타데이터 저장 실패
            # → attrs는 파이프라인 내 임시 상태이므로 저장 시 제거
            df_save = df.copy()
            df_save.attrs = {}
            df_save.to_parquet(self._path(cache_key), compression="snappy")
        except Exception as exc:
            logger.error("결과 캐시 저장 실패 (%s): %s", cache_key[:16], exc)
            self._path(cache_key).unlink(missing_ok=True)

    def delete(self, cache_key: str) -> bool:
        path = self._path(cache_key)
        if path.exists():
            path.unlink()
            return True
        return False

    def clear_all(self) -> int:
        """캐시 디렉터리의 모든 Parquet 파일을 삭제. 삭제 수 반환."""
        count = 0
        for f in self._dir.glob("*.parquet"):
            f.unlink()
            count += 1
        logger.info("결과 캐시 전체 삭제: %d개", count)
        return count

    def keys(self) -> list[str]:
        """저장된 캐시 키 목록."""
        return sorted(f.stem for f in self._dir.glob("*.parquet"))

    def total_size_mb(self) -> float:
        total = sum(f.stat().st_size for f in self._dir.glob("*.parquet"))
        return round(total / (1024 * 1024), 3)
