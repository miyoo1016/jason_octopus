"""
네이버 증권 API 기반 KRX 데이터 수집 어댑터.
pykrx의 KRX OpenAPI가 차단/변경되어 네이버 증권으로 대체합니다.

Rate Limiting:
  - 요청 간 랜덤 지터 대기 (0.08~0.15초)
  - 실패 시 exponential backoff 재시도 (1s→2s→4s, 최대 3회)
  - 개별 종목 실패 시 해당 종목만 건너뛰고 나머지 계속 진행
"""
import logging
import random
import time
import requests
import pandas as pd
from pathlib import Path
from typing import Any, Literal

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

Market = Literal["KOSPI", "KOSDAQ", "ALL"]
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# 재시도 가능한 HTTP 상태 코드
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class _RetryableHTTPError(Exception):
    """재시도 가능한 HTTP 오류."""


class NaverKRXClient:
    """네이버 증권 모바일 API 기반 데이터 클라이언트.

    모든 API 호출에 rate limiting(random jitter 0.08~0.15s)과
    exponential backoff retry(최대 3회)를 적용합니다.
    """

    def __init__(self, cache_dir: str | Path = ".cache") -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _UA})

    @retry(
        retry=retry_if_exception_type((_RetryableHTTPError, requests.ConnectionError, requests.Timeout)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _api_get(self, url: str, timeout: int = 10) -> Any:
        """공통 API GET 호출 + jitter 대기 + 재시도.

        Returns:
            응답 JSON (dict 또는 list)

        Raises:
            _RetryableHTTPError: 429/5xx → tenacity가 자동 재시도
            requests.HTTPError: 4xx (429 제외) → 재시도 없이 즉시 실패
        """
        time.sleep(random.uniform(0.08, 0.15))  # jitter delay
        resp = self._session.get(url, timeout=timeout)
        if resp.status_code in _RETRYABLE_STATUS:
            raise _RetryableHTTPError(
                f"HTTP {resp.status_code} for {url[:80]}..."
            )
        resp.raise_for_status()
        return resp.json()

    # ── 유니버스 ──
    def get_universe(self, as_of_date: str, market: Market = "ALL", **kw) -> pd.DataFrame:
        import datetime
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        cache_path = self._cache_dir / f"universe_{as_of_date}_{market}.parquet"
        
        # 오늘 날짜면 실시간 데이터를 위해 캐시 우회
        if as_of_date != today_str and cache_path.exists():
            logger.info("유니버스 캐시 적중: %s", cache_path.name)
            df_cached = pd.read_parquet(cache_path)
            return df_cached[df_cached["code"].str.match(r'^\d{6}$', na=False)].reset_index(drop=True)

        markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
        all_rows = []

        for mkt in markets:
            page = 1
            while True:
                url = f"https://m.stock.naver.com/api/stocks/marketValue/{mkt}?page={page}&pageSize=100"
                try:
                    data = self._api_get(url)
                except Exception as e:
                    logger.warning("네이버 API 호출 실패 (page=%d): %s", page, e)
                    break

                stocks = data.get("stocks", [])
                if not stocks:
                    break

                for s in stocks:
                    all_rows.append({
                        "code":       s.get("itemCode", ""),
                        "name":       s.get("stockName", ""),
                        "market":     mkt,
                        "close":      _parse_int(s.get("closePrice", "0")),
                        "volume":     _parse_int(s.get("accumulatedTradingVolume", "0")),
                        "market_cap": _parse_int(s.get("marketValue", "0")) * 1_000_000,
                    })

                page += 1

                if len(stocks) < 100:
                    break

            logger.info("%s 수집 완료: %d 종목", mkt, sum(1 for r in all_rows if r["market"] == mkt))

        df = pd.DataFrame(all_rows)
        # 6자리 숫자 코드만 유지 (채권·파생·구조화상품 등 비표준 코드 제거)
        df = df[df["code"].str.match(r'^\d{6}$', na=False)].reset_index(drop=True)
        if len(df) > 0:
            df.to_parquet(cache_path, index=False)
        return df

    # ── 외국인/기관 수급 (공통 trend API) ──
    def _fetch_flow_batch(
        self,
        codes: list[str],
        n_days: int,
        as_of_date: str = "",
    ) -> dict[str, list[tuple[pd.Timestamp, int, int]]]:
        """종목별 (날짜, 외국인 순매수, 기관 순매수) 리스트 병렬 반환."""
        from concurrent.futures import ThreadPoolExecutor
        as_of_ts = pd.to_datetime(as_of_date) if as_of_date else None
        pages_needed = (n_days + 30) // 60 + 1
        
        out: dict[str, list[tuple[pd.Timestamp, int, int]]] = {}
        
        def _fetch_single(code):
            filtered = []
            try:
                for page in range(1, pages_needed + 1):
                    url = f"https://m.stock.naver.com/api/stock/{code}/trend?page={page}&pageSize=60"
                    rows = self._api_get(url, timeout=5)
                    if not isinstance(rows, list):
                        rows = rows.get("datas", [])
                    if not rows: break
                    for r in rows:
                        biz = str(r.get("bizdate", ""))[:8]
                        if not biz or len(biz) != 8: continue
                        biz_ts = pd.to_datetime(biz, format="%Y%m%d")
                        if as_of_ts is not None and biz_ts > as_of_ts: continue
                        f_buy = _parse_signed(r.get("foreignerPureBuyQuant", "0"))
                        o_buy = _parse_signed(r.get("organPureBuyQuant", "0"))
                        filtered.append((biz_ts, f_buy, o_buy))
                filtered.sort(key=lambda x: x[0], reverse=True)
                return code, filtered[:n_days]
            except Exception:
                return code, []

        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(_fetch_single, codes))
            for code, data in results:
                out[code] = data
        return out

    def get_foreign_flow(self, universe_df: pd.DataFrame, as_of_date: str, n_days: int = 5, **kw) -> pd.DataFrame:
        codes = universe_df["code"].tolist()[:300]
        # n_days가 5일이라도, 장기 수급 로직을 위해 120일을 가져오도록 변경 (상위 호출자에서 120일을 넘겨줌)
        flow_days = max(n_days, 120) 
        flow = self._fetch_flow_batch(codes, flow_days, as_of_date=as_of_date)
        df = universe_df.copy()
        
        def _calc_foreign_flow(c):
            hist = flow.get(c, [])
            if not hist: return 0
            # 단기 순매수
            return sum(r[1] for r in hist[:n_days])
            
        df["foreign_net_buy"] = df["code"].map(_calc_foreign_flow).fillna(0).astype(int)
        
        # 내부적으로 전체 플로우 데이터를 데이터프레임 속성에 캐싱하여 노드가 쓸 수 있게 함
        # pandas에서는 보통 _를 붙여서 숨겨진 속성으로 전달 가능
        df.attrs["foreign_flow_hist"] = flow
        
        logger.info("외국인 수급 완료: %d종목 (as_of=%s)", len(df), as_of_date)
        return df

    def get_institution_flow(self, universe_df: pd.DataFrame, as_of_date: str, n_days: int = 5, **kw) -> pd.DataFrame:
        codes = universe_df["code"].tolist()[:300]
        flow = self._fetch_flow_batch(codes, n_days, as_of_date=as_of_date)
        df = universe_df.copy()
        
        def _calc_inst_flow(c):
            hist = flow.get(c, [])
            if not hist: return 0
            return sum(r[2] for r in hist[:n_days])
            
        df["institution_net_buy"] = df["code"].map(_calc_inst_flow).fillna(0).astype(int)
        return df

    def get_market_ohlcv(self, date_str: str, market: str = "ALL") -> pd.DataFrame:
        """특정 날짜의 시장 전체 종목 OHLCV를 반환합니다 (pykrx 사용)."""
        from pykrx import stock
        from data.holidays import to_krx_date
        
        target_date = to_krx_date(date_str)
        markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
        
        dfs = []
        for m in markets:
            try:
                df = stock.get_market_ohlcv_by_date(target_date, target_date, m)
                if not df.empty:
                    df = df.reset_index()
                    df["market"] = m
                    dfs.append(df)
            except Exception as e:
                logger.error(f"시장 OHLCV 수집 실패 ({m}, {target_date}): {e}")
        
        if not dfs:
            return pd.DataFrame()
            
        combined = pd.concat(dfs, ignore_index=True)
        # 컬럼명 통일 (종목코드, 종가 등)
        combined = combined.rename(columns={
            "티커": "code",
            "종목명": "name",
            "종가": "close",
            "거래량": "volume",
            "시가총액": "market_cap"
        })
        return combined

    def get_ohlcv(self, code: str, start_date: str = "", end_date: str = "", pages: int = 6) -> pd.DataFrame:
        """단일 종목의 OHLCV를 수집합니다.

        과거 데이터(end_date < 오늘)는 디스크 캐시를 사용하여 반복 호출을 방지합니다.
        캐시 키: ohlcv_{code}_{end_date}_{pages}.parquet
        start_date는 캐시 이후 필터로 적용되므로 다른 start_date 호출도 같은 캐시를 재사용합니다.
        """
        end_ts   = pd.to_datetime(end_date) if end_date else None
        start_ts = pd.to_datetime(start_date) if start_date else None
        today_str = pd.Timestamp.now().strftime("%Y-%m-%d")

        # end_date가 지정된 경우 캐시 사용 (오늘 포함 — 동일 파이프라인 내 반복 수집 방지)
        use_cache = bool(end_date) and end_date <= today_str
        cache_path = (self._cache_dir / f"ohlcv_{code}_{end_date}_{pages}.parquet") if use_cache else None

        if use_cache and cache_path is not None and cache_path.exists():
            try:
                df_cached = pd.read_parquet(cache_path)
                logger.debug("OHLCV 캐시 적중: %s", cache_path.name)
                if start_ts is not None:
                    return df_cached[df_cached.index >= start_ts]
                return df_cached
            except Exception:
                cache_path.unlink(missing_ok=True)

        rows: list[dict] = []
        try:
            for page in range(1, pages + 1):
                url = (
                    f"https://m.stock.naver.com/api/stock/{code}/price"
                    f"?page={page}&pageSize=60"
                )
                try:
                    body = self._api_get(url, timeout=5)
                except Exception:
                    break

                prices = body if isinstance(body, list) else body.get("datas", body.get("priceInfos", []))
                if not isinstance(prices, list) or not prices:
                    break

                for p in prices:
                    rows.append({
                        "date":   str(p.get("localTradedAt", p.get("date", "")))[:10],
                        "open":   _parse_int(p.get("openPrice",  p.get("open",   0))),
                        "high":   _parse_int(p.get("highPrice",  p.get("high",   0))),
                        "low":    _parse_int(p.get("lowPrice",   p.get("low",    0))),
                        "close":  _parse_int(p.get("closePrice", p.get("close",  0))),
                        "volume": _parse_int(p.get("accumulatedTradingVolume", p.get("volume", 0))),
                    })

                if start_ts and rows:
                    last_date = pd.to_datetime(rows[-1]["date"])
                    if last_date < start_ts:
                        break

            if not rows:
                return pd.DataFrame()

            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).set_index("date").sort_index()

            if end_ts:
                df = df[df.index <= end_ts]

            # 캐시 저장 (start_ts 필터 전 — 다른 start_date도 재사용 가능하도록)
            if use_cache and cache_path is not None and not df.empty:
                try:
                    df.to_parquet(cache_path)
                except Exception as e:
                    logger.warning("OHLCV 캐시 저장 실패 (%s): %s", code, e)

            if start_ts:
                df = df[df.index >= start_ts]
            return df
        except Exception as e:
            logger.warning(f"OHLCV 수집 실패 ({code}): {e}")
            return pd.DataFrame()

    def get_ohlcv_batch(self, codes: list[str], start_date: str = "", end_date: str = "", **kw) -> dict[str, pd.DataFrame]:
        """종목별 OHLCV를 고속 병렬로 수집합니다."""
        from concurrent.futures import ThreadPoolExecutor
        
        results = {}
        def _fetch_one(code):
            try:
                # 6페이지(360일) 수집
                df = self.get_ohlcv(code, start_date=start_date, end_date=end_date, pages=6)
                if not df.empty:
                    return code, df
            except Exception as e:
                logger.debug(f"Batch OHLCV 수집 실패 ({code}): {e}")
            return code, pd.DataFrame()

        with ThreadPoolExecutor(max_workers=20) as executor:
            task_results = executor.map(_fetch_one, codes)
            for code, df in task_results:
                if not df.empty:
                    results[code] = df
        
        return results


def _parse_int(s) -> int:
    if isinstance(s, (int, float)):
        return int(s)
    cleaned = str(s).replace(",", "").replace(" ", "").strip()
    if not cleaned or cleaned == "-" or cleaned == "N/A":
        return 0
    try:
        # 소수점이 포함된 경우(시장 지수 등) float로 먼저 변환
        return int(float(cleaned))
    except (ValueError, OverflowError):
        return 0


def _parse_signed(s) -> int:
    """'+1,234,567' 또는 '-840,944' 형태의 부호 있는 정수 파싱."""
    if isinstance(s, (int, float)):
        return int(s)
    cleaned = str(s).replace(",", "").replace(" ", "").strip()
    if not cleaned or cleaned in ("-", "N/A"):
        return 0
    try:
        return int(float(cleaned))
    except (ValueError, OverflowError):
        return 0
