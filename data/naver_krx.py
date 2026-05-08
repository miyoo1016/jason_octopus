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
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

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
_FLOW_BODY_LOG_LIMIT = 1200


class _RetryableHTTPError(Exception):
    """재시도 가능한 HTTP 오류."""


# 전역 캐시 (pykrx 데이터 중복 요청 방지)
_PYKRX_CACHE = {
    "date": None,
    "foreign": {}, # mkt -> df
    "institution": {} # mkt -> df
}

# [신규] OHLCV 메모리 캐시 (동일 실행 세션 내 중복 요청 방지)
_OHLCV_MEM_CACHE = {}

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
        wait=wait_exponential(multiplier=1, min=1, max=4),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _api_get(self, url: str, timeout: int = 10) -> Any:
        """공통 API GET 호출 + jitter 대기 + 재시도."""
        time.sleep(random.uniform(0.1, 0.3))  # jitter delay 증가
        resp = self._session.get(url, timeout=timeout)
        if resp.status_code in _RETRYABLE_STATUS:
            raise _RetryableHTTPError(f"HTTP {resp.status_code} for {url[:80]}...")
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type((_RetryableHTTPError, requests.ConnectionError, requests.Timeout)),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _api_get_with_flow_trace(self, url: str, timeout: int = 10, *, code: str, page: int) -> Any:
        """수급 API 호출 원문을 추적하며 재시도를 수행합니다."""
        parsed = urlparse(url)
        params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}
        endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        
        # jitter delay: 50종목 이상 배치 시 더 긴 지연 부여
        time.sleep(random.uniform(0.2, 0.5))
        
        try:
            resp = self._session.get(url, timeout=timeout)
        except requests.Timeout as exc:
            logger.warning("[수급 API 타임아웃] code=%s page=%s error=%s", code, page, exc)
            raise _RetryableHTTPError(f"Timeout for {url[:80]}")
        except requests.RequestException as exc:
            logger.warning("[수급 API 요청 오류] code=%s page=%s error=%s", code, page, exc)
            raise

        body_sample = resp.text[:_FLOW_BODY_LOG_LIMIT]
        print(f"[수급 API 응답] status_code={resp.status_code} body_200={resp.text[:200]}")
        logger.info(
            "[수급 API 응답] code=%s page=%s http_status=%s body_sample=%s",
            code,
            page,
            resp.status_code,
            body_sample,
        )
        if resp.status_code in _RETRYABLE_STATUS:
            raise _RetryableHTTPError(f"HTTP {resp.status_code} for {url[:80]}...")
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
                        "market_cap": _parse_int(s.get("marketValue", "0")) * 100_000_000,
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
        diagnostics: dict[str, dict[str, Any]] = {}
        target_biz = as_of_date.replace("-", "") if as_of_date else ""

        def _fetch_single(code):
            filtered = []
            diag = {
                "source": "naver_trend",
                "api_error": None,
                "zero_reason": None,
                "latest_biz": None,
                "target_biz": target_biz,
                "response_rows": 0,
                "endpoint": f"https://m.stock.naver.com/api/stock/{code}/trend",
                "params": {"pageSize": 60},
            }
            try:
                for page in range(1, pages_needed + 1):
                    url = f"https://m.stock.naver.com/api/stock/{code}/trend?page={page}&pageSize=60"
                    rows = self._api_get_with_flow_trace(url, timeout=5, code=code, page=page)
                    if not isinstance(rows, list):
                        rows = rows.get("datas", [])
                    if not rows:
                        break
                    diag["response_rows"] += len(rows)
                    for r in rows:
                        biz = str(r.get("bizdate", ""))[:8]
                        if not biz or len(biz) != 8: continue
                        biz_ts = pd.to_datetime(biz, format="%Y%m%d")
                        if as_of_ts is not None and biz_ts > as_of_ts:
                            continue
                        f_buy = _parse_signed(r.get("foreignerPureBuyQuant", "0"))
                        o_buy = _parse_signed(r.get("organPureBuyQuant", "0"))
                        if target_biz and biz == target_biz and f_buy == 0 and o_buy == 0:
                            logger.info(
                                "[수급 0 판정] code=%s bizdate=%s reason=API_RESPONSE_ZERO foreignerPureBuyQuant=%r organPureBuyQuant=%r raw_row=%s",
                                code,
                                biz,
                                r.get("foreignerPureBuyQuant"),
                                r.get("organPureBuyQuant"),
                                str(r)[:_FLOW_BODY_LOG_LIMIT],
                            )
                        filtered.append((biz_ts, f_buy, o_buy))
                filtered.sort(key=lambda x: x[0], reverse=True)
                if filtered:
                    diag["latest_biz"] = filtered[0][0].strftime("%Y%m%d")
                    if target_biz and diag["latest_biz"] != target_biz:
                        diag["zero_reason"] = "NO_TARGET_DATE_ROW"
                        logger.warning(
                            "[수급 없음 판정] code=%s target_biz=%s latest_biz=%s reason=API_HAS_NO_TARGET_DATE_ROW",
                            code,
                            target_biz,
                            diag["latest_biz"],
                        )
                else:
                    diag["zero_reason"] = "EMPTY_RESPONSE"
                    logger.warning(
                        "[수급 없음 판정] code=%s target_biz=%s reason=EMPTY_OR_UNUSABLE_API_RESPONSE rows=%s",
                        code,
                        target_biz,
                        diag["response_rows"],
                    )
                return code, filtered[:n_days], diag
            except Exception as exc:
                diag["api_error"] = str(exc)
                diag["zero_reason"] = "API_TIMEOUT_OR_ERROR"
                logger.warning("[수급 API 실패 판정] code=%s reason=API_TIMEOUT_OR_ERROR error=%s", code, exc)
                return code, [], diag

        # [안전장치] 최대 150종목으로 제한 (서버 부하 방지)
        codes = codes[:150]
        
        with ThreadPoolExecutor(max_workers=5) as executor: # 워커 수 10 -> 5로 대폭 하향 (안정성 우선)
            results = list(executor.map(_fetch_single, codes))
            success_count = 0
            for code, data, diag in results:
                out[code] = data
                diagnostics[code] = diag
                if not diag.get("api_error"):
                    success_count += 1
            
            logger.info("[수급 수집 리포트] 성공=%d/%d (성공률 %.1f%%)", 
                        success_count, len(codes), (success_count/len(codes)*100 if codes else 0))

        out["_diagnostics"] = diagnostics  # type: ignore[assignment]
        return out

    def get_foreign_flow(self, universe_df: pd.DataFrame, as_of_date: str, n_days: int = 5, **kw) -> pd.DataFrame:
        # [안전장치] 수급 분석은 상위 150개만 진행 (OR 조건 시 폭주 방지)
        codes = universe_df["code"].tolist()[:150]
        flow_days = max(n_days, 60) # 분석 기간 120 -> 60으로 단축 (속도 향상)
        flow = self._fetch_flow_batch(codes, flow_days, as_of_date=as_of_date)
        flow_diag = flow.pop("_diagnostics", {})
        df = universe_df.copy()
        market_close_warning = _should_show_flow_close_warning(as_of_date)

        from data.holidays import prev_trading_day as _prev_td
        target_biz = as_of_date.replace("-", "")
        prev_biz   = _prev_td(as_of_date).replace("-", "")   # 직전 거래일 (주말·공휴일 제외)
        fallback_codes: set = set()

        def _calc_foreign_flow(c):
            hist = flow.get(c, [])
            if not hist:
                logger.warning("[외국인 수급 없음] code=%s reason=%s", c, flow_diag.get(c, {}).get("zero_reason"))
                return pd.NA
            latest_biz = hist[0][0].strftime("%Y%m%d")

            if latest_biz == target_biz:
                # ① 당일 데이터 정상 수신
                value = hist[0][1]
                logger.info(
                    "[외국인 수급 당일] code=%s value=%s source=%s",
                    c, value, flow_diag.get(c, {}).get("source"),
                )
                return value

            if latest_biz == prev_biz:
                # ② 직전 거래일 데이터로 폴백 (KRX 집계 지연, 1거래일 이내)
                value = hist[0][1]
                fallback_codes.add(c)
                logger.info("[외국인 수급 전일 폴백] code=%s target=%s latest=%s value=%s",
                            c, target_biz, latest_biz, value)
                return value

            # ③ 2거래일 이상 지연 → 데이터 신뢰 불가
            logger.warning("[외국인 수급 없음] code=%s target=%s latest=%s reason=STALE_DATA",
                           c, target_biz, latest_biz)
            return pd.NA

        df["foreign_net_buy"] = pd.to_numeric(df["code"].map(_calc_foreign_flow), errors="coerce").astype("Int64")
        # [수정] 전체 일괄 경고가 아닌, 종목별로 개별 라벨 적용 (삼성E&A 억울함 해소)
        df["flow_data_warning"] = "✅ 수급: 당일 실시간 반영"
        if fallback_codes:
            df.loc[df["code"].isin(fallback_codes), "flow_data_warning"] = "⚠️ 수급: 전일 기준 (당일 API 미집계)"
        
        # 데이터가 아예 없는 경우 (NaN) 처리
        mask_nan = df["foreign_net_buy"].isna()
        if mask_nan.any():
            df.loc[mask_nan, "flow_data_warning"] = "⚠️ 수급 데이터: 장 마감 후 갱신"
        
        df.attrs["foreign_flow_hist"] = flow
        df.attrs["foreign_flow_diagnostics"] = flow_diag
        return df

    def get_institution_flow(self, universe_df: pd.DataFrame, as_of_date: str, n_days: int = 5, **kw) -> pd.DataFrame:
        # [안전장치] 기관 수급 상위 150개 제한
        codes = universe_df["code"].tolist()[:150]
        flow_days = max(n_days, 60)
        flow = self._fetch_flow_batch(codes, flow_days, as_of_date=as_of_date)
        flow_diag = flow.pop("_diagnostics", {})
        df = universe_df.copy()
        
        from data.holidays import prev_trading_day as _prev_td_inst
        target_biz_inst = as_of_date.replace("-", "")
        prev_biz_inst   = _prev_td_inst(as_of_date).replace("-", "")
        fallback_codes_inst: set = set()

        def _calc_inst_flow(c):
            hist = flow.get(c, [])
            if not hist: return pd.NA
            latest_biz = hist[0][0].strftime("%Y%m%d")
            if latest_biz == target_biz_inst: return hist[0][2]
            if latest_biz == prev_biz_inst:
                fallback_codes_inst.add(c)
                return hist[0][2]
            return pd.NA

        df["institution_net_buy"] = pd.to_numeric(df["code"].map(_calc_inst_flow), errors="coerce").astype("Int64")
        # [수정] 기관 수급도 동일하게 개별 라벨 적용
        df["flow_data_warning"] = "✅ 수급: 당일 실시간 반영"
        if fallback_codes_inst:
            df.loc[df["code"].isin(fallback_codes_inst), "flow_data_warning"] = "⚠️ 수급: 전일 기준 (당일 API 미집계)"
            
        mask_nan_inst = df["institution_net_buy"].isna()
        if mask_nan_inst.any():
            df.loc[mask_nan_inst, "flow_data_warning"] = "⚠️ 수급 데이터: 장 마감 후 갱신"
        
        df.attrs["institution_flow_hist"] = flow
        df.attrs["institution_flow_diagnostics"] = flow_diag
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

        # 메모리 캐시 확인 (가장 빠름)
        cache_key = f"{code}_{pages}_{end_date}"
        if cache_key in _OHLCV_MEM_CACHE:
            df_mem = _OHLCV_MEM_CACHE[cache_key]
            if start_ts is not None:
                return df_mem[df_mem.index >= start_ts]
            return df_mem

        rows: list[dict] = []
        try:
            # [최적화] pageSize 60 -> 120 (요청 횟수 절반 감소)
            p_size = 120
            p_count = (pages * 60 + p_size - 1) // p_size
            
            for page in range(1, p_count + 1):
                url = (
                    f"https://m.stock.naver.com/api/stock/{code}/price"
                    f"?page={page}&pageSize={p_size}"
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
            
            # 메모리 캐시에 보관
            _OHLCV_MEM_CACHE[cache_key] = df
            return df
        except Exception as e:
            logger.warning(f"OHLCV 수집 실패 ({code}): {e}")
            return pd.DataFrame()

    def get_ohlcv_batch(self, codes: list[str], start_date: str = "", end_date: str = "", pages: int = 3, **kw) -> dict[str, pd.DataFrame]:
        """종목별 OHLCV를 고속 병렬로 수집합니다."""
        # [안전장치] 한 번에 최대 200종목까지만 배치 조회 허용
        codes = codes[:200]
        from concurrent.futures import ThreadPoolExecutor

        results = {}
        def _fetch_one(code):
            try:
                # 지정된 페이지 수만큼만 수집 (기본 3페이지=180일)
                df = self.get_ohlcv(code, start_date=start_date, end_date=end_date, pages=pages)
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


def _should_show_flow_close_warning(as_of_date: str) -> bool:
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    today_kst = now_kst.strftime("%Y-%m-%d")
    return as_of_date == today_kst and now_kst.time() < dt_time(15, 30)


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
        return None  # 0이 아닌 None 반환하여 수집 실패와 0을 구분


def _extract_net_purchase_qty(df: pd.DataFrame, ticker: str) -> int:
    if df is None or df.empty or ticker not in df.index:
        return 0
    row = df.loc[ticker]
    for col in row.index:
        col_name = str(col)
        if "순매수" in col_name and "수량" in col_name:
            return _parse_signed(row[col])
    if len(row.index) >= 3:
        return _parse_signed(row.iloc[2])
    return 0
