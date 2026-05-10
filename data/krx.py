"""
KRX 시세·수급 데이터 어댑터 (pykrx 래퍼).

핵심 설계 원칙:
- 모든 날짜는 공개 API에서 'YYYY-MM-DD', 내부(pykrx)에서 'YYYYMMDD'
- pykrx는 T-1(전일) 기준 — 장 마감 후 18:00 KST 이후 확정
- 2,700종목 전체 스캔 시 배치 처리로 속도 최적화
- 캐시 레이어를 통해 중복 호출 방지

표준 DataFrame 컬럼 (모든 노드가 공유):
    code    str   종목코드 (6자리)
    name    str   종목명
    market  str   'KOSPI' 또는 'KOSDAQ'
    close   float 종가
    volume  int   거래량

수급 컬럼 (foreign_flow, institution_flow 노드가 추가):
    foreign_net_buy   int | None   외국인 당일 순매수량 (주)
    institution_net_buy int | None 기관 당일 순매수량 (주)

사용법:
    from data.krx import KRXClient
    from backend.config import settings

    client = KRXClient(cache_dir=settings.data_cache_dir)
    universe = client.get_universe("2026-05-05")
    universe_with_flow = client.get_foreign_flow(universe, "2026-05-05", n_days=5)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal

import pandas as pd
from pykrx import stock as pykrx_stock

from data.cache import DataCache
from data.holidays import to_krx_date, prev_trading_day, trading_days_between

logger = logging.getLogger(__name__)

Market = Literal["KOSPI", "KOSDAQ", "ALL"]

# pykrx 호출 사이 딜레이 (서버 부하 방지, 초)
_CALL_DELAY_S = 0.05


class KRXClient:
    """
    KRX 데이터 수집 클라이언트.
    모든 메서드는 캐시를 먼저 확인하고, 없으면 pykrx로 수집합니다.
    """

    def __init__(self, cache_dir: str | Path) -> None:
        self._cache = DataCache(cache_dir)

    # ── 유니버스 ──────────────────────────────────────────────────────────────

    def get_universe(
        self,
        as_of_date: str,
        market: Market = "ALL",
        *,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        KOSPI/KOSDAQ 전 종목 리스트를 반환합니다.

        Args:
            as_of_date:    기준일 'YYYY-MM-DD' (pykrx T-1 기준)
            market:        'KOSPI', 'KOSDAQ', 'ALL'
            force_refresh: 캐시 무시 여부

        Returns:
            DataFrame [code, name, market, close, volume, market_cap]
        """
        key = self._cache.make_key("universe", as_of_date, market.lower())

        def fetch():
            markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
            frames = []
            krx_date = to_krx_date(as_of_date)

            for mkt in markets:
                try:
                    tickers = pykrx_stock.get_market_ticker_list(krx_date, market=mkt)
                    time.sleep(_CALL_DELAY_S)

                    if not tickers:
                        logger.warning("%s 종목 리스트 비어있음 (날짜: %s)", mkt, as_of_date)
                        continue

                    # 종목명
                    names = {t: pykrx_stock.get_market_ticker_name(t) for t in tickers}
                    time.sleep(_CALL_DELAY_S)

                    # 시가총액 + 종가 + 거래량 (전체 시장 한 번에)
                    cap_df = pykrx_stock.get_market_cap_by_ticker(krx_date, market=mkt)
                    time.sleep(_CALL_DELAY_S)

                    # OHLCV (종가, 거래량)
                    ohlcv_df = pykrx_stock.get_market_ohlcv_by_date(
                        krx_date, krx_date, mkt
                    )
                    time.sleep(_CALL_DELAY_S)

                    rows = []
                    for ticker in tickers:
                        row = {
                            "code":       ticker,
                            "name":       names.get(ticker, ""),
                            "market":     mkt,
                            "close":      float(cap_df.loc[ticker, "종가"])    if ticker in cap_df.index else 0.0,
                            "volume":     int(cap_df.loc[ticker, "거래량"])    if ticker in cap_df.index else 0,
                            "market_cap": int(cap_df.loc[ticker, "시가총액"]) if ticker in cap_df.index else 0,
                        }
                        rows.append(row)

                    frames.append(pd.DataFrame(rows))

                except Exception as exc:
                    logger.error("%s 유니버스 수집 실패 (%s): %s", mkt, as_of_date, exc)

            if not frames:
                return pd.DataFrame(columns=["code", "name", "market", "close", "volume", "market_cap"])

            df = pd.concat(frames, ignore_index=True)
            # 거래량 0인 종목(거래정지·관리종목) 제외
            df = df[df["volume"] > 0].reset_index(drop=True)
            df["code"] = df["code"].astype(str).str.zfill(6)
            return df

        df = self._cache.load_or_fetch(key, fetch, force_refresh=force_refresh)
        logger.info("유니버스 로드 완료: %d종목 (기준일 %s)", len(df), as_of_date)
        return df

    # ── OHLCV (단일 종목) ─────────────────────────────────────────────────────

    def get_ohlcv(
        self,
        code: str,
        start_date: str,
        end_date: str,
        *,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        단일 종목 OHLCV를 반환합니다.

        Args:
            code:       종목코드 (6자리)
            start_date: 시작일 'YYYY-MM-DD'
            end_date:   종료일 'YYYY-MM-DD'

        Returns:
            DataFrame [date(index), open, high, low, close, volume]
        """
        key = self._cache.make_key("ohlcv", code, start_date, end_date)

        def fetch():
            try:
                df = pykrx_stock.get_market_ohlcv_by_ticker(
                    to_krx_date(start_date),
                    to_krx_date(end_date),
                    code,
                )
                time.sleep(_CALL_DELAY_S)
                df.index = pd.to_datetime(df.index)
                df.columns = ["open", "high", "low", "close", "volume",
                               "turnover", "change_rate", "change_amount"][:len(df.columns)]
                # 필요 컬럼만 유지
                cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
                return df[cols]
            except Exception as exc:
                logger.error("OHLCV 수집 실패 (%s %s~%s): %s", code, start_date, end_date, exc)
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        return self._cache.load_or_fetch(key, fetch, force_refresh=force_refresh)

    # ── 외국인 수급 ───────────────────────────────────────────────────────────

    def get_foreign_flow(
        self,
        universe: pd.DataFrame,
        as_of_date: str,
        n_days: int = 5,
        *,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        유니버스 전 종목의 외국인 당일 순매수량을 universe에 합쳐 반환합니다.

        Args:
            universe:   get_universe() 반환 DataFrame
            as_of_date: 기준일 'YYYY-MM-DD'
            n_days:     호환성용 인자. 수집 기간은 항상 당일로 고정합니다.

        Returns:
            universe + foreign_net_buy 컬럼 추가된 DataFrame
        """
        key = self._cache.make_key("foreign_flow_daily", as_of_date)

        def fetch():
            krx_start = to_krx_date(as_of_date)
            krx_end = to_krx_date(as_of_date)

            all_rows = []
            for mkt in ["KOSPI", "KOSDAQ"]:
                try:
                    df = pykrx_stock.get_market_net_purchases_of_equities_by_ticker(
                        krx_start, krx_end, mkt, "외국인"
                    )
                    time.sleep(_CALL_DELAY_S)

                    if df is None or df.empty:
                        continue

                    # 컬럼명 정규화 (pykrx 버전에 따라 다름)
                    net_col = None
                    for col in df.columns:
                        if "순매수" in str(col) and "수량" in str(col):
                            net_col = col
                            break
                    if net_col is None and len(df.columns) >= 3:
                        net_col = df.columns[2]   # 보통 3번째 컬럼이 순매수량

                    if net_col:
                        sub = df[[net_col]].copy()
                        sub.columns = ["foreign_net_buy"]
                        sub.index.name = "code"
                        all_rows.append(sub)

                except Exception as exc:
                    logger.error("외국인 수급 수집 실패 (%s %s): %s", mkt, as_of_date, exc)

            if not all_rows:
                return pd.DataFrame(columns=["code", "foreign_net_buy"])

            flow_df = pd.concat(all_rows)
            flow_df = flow_df.reset_index()
            flow_df["code"] = flow_df["code"].astype(str).str.zfill(6)
            flow_df["foreign_net_buy"] = pd.to_numeric(flow_df["foreign_net_buy"], errors="coerce").astype("Int64")
            return flow_df

        flow = self._cache.load_or_fetch(key, fetch, force_refresh=force_refresh)

        if "foreign_net_buy" not in flow.columns:
            logger.warning("외국인 수급 데이터 없음 — None으로 표시합니다.")
            universe["foreign_net_buy"] = pd.Series([pd.NA] * len(universe), dtype="Int64")
            return universe

        merged = universe.merge(flow[["code", "foreign_net_buy"]], on="code", how="left")
        merged["foreign_net_buy"] = pd.to_numeric(merged["foreign_net_buy"], errors="coerce").astype("Int64")
        logger.info("외국인 수급 병합 완료: %d종목", len(merged))
        return merged

    # ── 기관 수급 ─────────────────────────────────────────────────────────────

    def get_institution_flow(
        self,
        universe: pd.DataFrame,
        as_of_date: str,
        n_days: int = 5,
        *,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        유니버스 전 종목의 기관 당일 순매수량을 universe에 합쳐 반환합니다.

        Args:
            universe:   get_universe() 반환 DataFrame
            as_of_date: 기준일 'YYYY-MM-DD'
            n_days:     호환성용 인자. 수집 기간은 항상 당일로 고정합니다.

        Returns:
            universe + institution_net_buy 컬럼 추가된 DataFrame
        """
        key = self._cache.make_key("institution_flow_daily", as_of_date)

        def fetch():
            krx_start = to_krx_date(as_of_date)
            krx_end = to_krx_date(as_of_date)

            all_rows = []
            for mkt in ["KOSPI", "KOSDAQ"]:
                try:
                    df = pykrx_stock.get_market_net_purchases_of_equities_by_ticker(
                        krx_start, krx_end, mkt, "기관합계"
                    )
                    time.sleep(_CALL_DELAY_S)

                    if df is None or df.empty:
                        continue

                    net_col = None
                    for col in df.columns:
                        if "순매수" in str(col) and "수량" in str(col):
                            net_col = col
                            break
                    if net_col is None and len(df.columns) >= 3:
                        net_col = df.columns[2]

                    if net_col:
                        sub = df[[net_col]].copy()
                        sub.columns = ["institution_net_buy"]
                        sub.index.name = "code"
                        all_rows.append(sub)

                except Exception as exc:
                    logger.error("기관 수급 수집 실패 (%s %s): %s", mkt, as_of_date, exc)

            if not all_rows:
                return pd.DataFrame(columns=["code", "institution_net_buy"])

            flow_df = pd.concat(all_rows).reset_index()
            flow_df["code"] = flow_df["code"].astype(str).str.zfill(6)
            flow_df["institution_net_buy"] = pd.to_numeric(flow_df["institution_net_buy"], errors="coerce").astype("Int64")
            return flow_df

        flow = self._cache.load_or_fetch(key, fetch, force_refresh=force_refresh)

        if "institution_net_buy" not in flow.columns:
            logger.warning("기관 수급 데이터 없음 — None으로 표시합니다.")
            universe["institution_net_buy"] = pd.Series([pd.NA] * len(universe), dtype="Int64")
            return universe

        merged = universe.merge(flow[["code", "institution_net_buy"]], on="code", how="left")
        merged["institution_net_buy"] = pd.to_numeric(merged["institution_net_buy"], errors="coerce").astype("Int64")
        logger.info("기관 수급 병합 완료: %d종목", len(merged))
        return merged

    # ── 시장 전체 OHLCV (특정일) ─────────────────────────────────────────────

    def get_market_ohlcv_snapshot(
        self,
        as_of_date: str,
        market: Market = "ALL",
        *,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        특정일 시장 전체 OHLCV를 종목코드 인덱스로 반환합니다.
        VCP·박스권 등 시그널 계산 시 각 종목 OHLCV를 개별 호출하는 대신
        이 메서드로 한 번에 받아서 필터링하면 훨씬 빠릅니다.

        Returns:
            DataFrame [code(index), open, high, low, close, volume]
        """
        key = self._cache.make_key("market_snapshot", as_of_date, market.lower())

        def fetch():
            markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
            frames = []
            krx_date = to_krx_date(as_of_date)
            for mkt in markets:
                try:
                    df = pykrx_stock.get_market_ohlcv_by_date(krx_date, krx_date, mkt)
                    time.sleep(_CALL_DELAY_S)
                    if df is not None and not df.empty:
                        frames.append(df)
                except Exception as exc:
                    logger.error("시장 스냅샷 수집 실패 (%s %s): %s", mkt, as_of_date, exc)

            if not frames:
                return pd.DataFrame()

            combined = pd.concat(frames)
            combined.index.name = "code"
            combined.index = combined.index.astype(str).str.zfill(6)
            return combined

        return self._cache.load_or_fetch(key, fetch, force_refresh=force_refresh)

    # ── 다기간 OHLCV (배치) ───────────────────────────────────────────────────

    def get_ohlcv_batch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        force_refresh: bool = False,
    ) -> dict[str, pd.DataFrame]:
        """
        여러 종목의 OHLCV를 한 번에 수집합니다.

        Args:
            codes:      종목코드 리스트
            start_date: 시작일 'YYYY-MM-DD'
            end_date:   종료일 'YYYY-MM-DD'

        Returns:
            {code: ohlcv_dataframe} 딕셔너리
        """
        result: dict[str, pd.DataFrame] = {}
        for code in codes:
            try:
                result[code] = self.get_ohlcv(
                    code, start_date, end_date, force_refresh=force_refresh
                )
            except Exception as exc:
                logger.error("배치 OHLCV 실패 (%s): %s", code, exc)
                result[code] = pd.DataFrame()
        return result
