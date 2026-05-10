"""
상대 강도(RS) 평가 노드.
"""
import pandas as pd
import logging
import hashlib
from datetime import datetime
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from engine.leakage_guard import assert_no_future_data
from data.holidays import prev_trading_day
from backend.algo_settings import algo_settings

logger = logging.getLogger(__name__)

class RsRatingParams(BaseModel):
    lookback_days: int = 252
    min_rating: int = 80  # UI에서 동적으로 조절 가능하도록 추가

class RsRatingNode(BaseNode):
    NODE_TYPE      = "rs_rating"
    DISPLAY_NAME   = "상대 강도(RS) 평가"
    DESCRIPTION    = "KOSPI 대비 최근 52주 초과 수익률 백분위 평가."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("rs_rating", "rs_score", "rs_status", "rs_flag")
    ParamsModel    = RsRatingParams

    def run(self, inputs: list[pd.DataFrame], params: RsRatingParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0]
        if df.empty or not context.krx_client:
            return df

        as_of = context.as_of_date
        start_date = prev_trading_day(as_of, n=params.lookback_days)

        # 1. 상위 100대 종목(시총 기준) + 059500 KOSPI ETF를 벤치마크로 사용
        universe = context.krx_client.get_universe(as_of)
        if universe.empty:
            df["rs_rating"] = None
            df["rs_score"] = None
            df["rs_status"] = "DATA_MISSING"
            df["rs_flag"] = "DATA_MISSING"
            df["rs_warning"] = "RS 벤치마크 유니버스 데이터 수집 실패"
            return df

        benchmark_size = 30 if len(df) <= 30 else 100
        benchmark_codes = universe.sort_values("market_cap", ascending=False).head(benchmark_size)["code"].tolist()
        target_codes = df["code"].tolist()[:200]
        kospi_etf = "069500"
        all_codes = list(set(benchmark_codes + target_codes + [kospi_etf]))

        # 2. 단일 기간 페치 (start_date ~ as_of)
        p_count = 6 if context.is_single_analysis else 3
        period_ohlcv = context.krx_client.get_ohlcv_batch(all_codes, start_date=start_date, end_date=as_of, pages=p_count)

        # 3. 코드별 기간 수익률 계산 (첫 거래일 → 마지막 거래일)
        raw_returns: dict[str, float] = {}
        price_end_dates: dict[str, str] = {}
        
        for code, hist in period_ohlcv.items():
            if hist.empty or len(hist) < 2:
                continue
            c_start = hist["close"].iloc[0]
            c_end   = hist["close"].iloc[-1]
            if c_start > 0:
                raw_returns[code] = c_end / c_start - 1.0
                price_end_dates[code] = hist.index[-1].strftime("%Y-%m-%d")

        if not raw_returns:
            logger.warning("RS: 수익률 데이터 수집 실패 — 모든 종목 rs_rating=None 처리")
            df["rs_rating"] = None
            df["rs_score"]  = None
            df["rs_status"] = "DATA_MISSING"
            df["rs_flag"] = "DATA_MISSING"
            df["rs_warning"] = "RS 가격 데이터 수집 실패"
            return df

        # 4. KOSPI 초과 수익률로 변환 후 전 종목 백분위 산출
        kospi_ret = raw_returns.get(kospi_etf)
        benchmark_end_date = price_end_dates.get(kospi_etf)
        
        if kospi_ret is None:
            logger.warning("RS: KOSPI benchmark (069500) 데이터 수집 실패 — 절대 수익률 기준으로 계산")
            excess = {code: ret for code, ret in raw_returns.items()}
            benchmark_warning = "KOSPI 데이터 수집 실패 (절대 수익률 기준)"
        else:
            excess = {code: ret - kospi_ret for code, ret in raw_returns.items()}
            benchmark_warning = None

        excess_series = pd.Series(excess)
        if len(excess_series) > 1:
            all_rs_ratings = (excess_series.rank(pct=True) * 98 + 1)
            all_rs_ranks = excess_series.rank(ascending=False)
        else:
            all_rs_ratings = excess_series.rank(pct=True) * 99.0
            all_rs_ranks = pd.Series({excess_series.index[0]: 1})
        
        # Freshness / Metadata 계산
        # 실제 가격 데이터의 마지막 거래일 확인
        max_price_end_date = max(price_end_dates.values()) if price_end_dates else None
        
        # FRESH: 기준 거래일(as_of) 데이터가 포함됨 (주말/휴장일 제외)
        # CACHE_VALID: 기준일이 동일하여 캐시 재사용 (DAG 엔진 캐시가 하겠지만 여기서는 데이터 관점)
        # STALE_MARKET_CLOSED: as_of가 주말/휴장이라 max_price_end_date가 이전인 경우 정상
        # STALE_UNEXPECTED: 영업일인데 데이터가 이전 날짜에 머물러 있는 경우
        
        from data.holidays import is_trading_day
        target_is_trading = is_trading_day(as_of)
        
        if max_price_end_date == as_of:
            freshness = "FRESH"
            staleness_reason = None
        elif not target_is_trading:
            freshness = "STALE_MARKET_CLOSED"
            staleness_reason = f"Market closed on {as_of}. Using last trading day {max_price_end_date}."
        elif max_price_end_date < as_of:
            # 영업일인데 데이터가 과거면 지연 또는 데이터 부재
            freshness = "STALE_UNEXPECTED"
            staleness_reason = f"Trading day {as_of} but data ends at {max_price_end_date}."
        else:
            freshness = "FRESH" # 미래 데이터는 assert_no_future_data에서 걸러짐
            staleness_reason = None

        # 5. 입력 종목 매핑 및 min_rating 필터
        min_rating = params.min_rating
        results = []

        # 캐시 키 구성 요소 시뮬레이션 (DAG 엔진이 생성한 키와는 별개로 노출용)
        universe_hash = hashlib.sha256(str(sorted(benchmark_codes)).encode()).hexdigest()[:8] if benchmark_codes else "none"
        simulated_cache_key = hashlib.sha256(f"{as_of}|{params.lookback_days}|{kospi_etf}|{universe_hash}".encode()).hexdigest()[:16]

        for _, row in df.iterrows():
            code   = row["code"]
            rating = all_rs_ratings.get(code)
            rank   = all_rs_ranks.get(code)
            price_end = price_end_dates.get(code)

            row_dict = row.to_dict()
            row_dict["rs_warning"] = benchmark_warning
            
            # Metadata 추가
            row_dict.update({
                "rs_as_of_date": as_of,
                "rs_price_end_date": price_end,
                "rs_benchmark_end_date": benchmark_end_date,
                "rs_lookback_window": params.lookback_days,
                "rs_benchmark_symbol": kospi_etf,
                "rs_universe_size": len(all_codes),
                "rs_cache_key": simulated_cache_key,
                "rs_cache_hit": getattr(context, "cache_hit", False), # Context에 있으면 사용
                "rs_source": "naver_ohlcv_batch",
                "rs_freshness_status": freshness,
                "rs_last_updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "rs_staleness_reason": staleness_reason,
                "rs_percentile": round(rating, 1) if rating is not None else None,
                "rs_rank": int(rank) if rank is not None else None,
            })

            if rating is None:
                row_dict["rs_rating"] = None
                row_dict["rs_score"]  = None
                row_dict["rs_status"] = "DATA_MISSING"
                row_dict["rs_flag"] = "DATA_MISSING"
                row_dict["rs_warning"] = (row_dict["rs_warning"] + " | " if row_dict["rs_warning"] else "") + "RS 데이터 수집 실패"
                results.append(row_dict)
            elif rating < min_rating:
                row_dict["rs_status"] = "Low"
                row_dict["rs_flag"] = "LOW_RS"
                row_dict["rs_rating"] = round(rating, 1)
                row_dict["rs_score"] = 10 if rating < 50 else 25
                results.append(row_dict)
            else:
                row_dict["rs_rating"] = round(rating, 1)
                row_dict["rs_score"]  = 60 if rating >= 90 else 40
                row_dict["rs_status"] = "Strong"
                row_dict["rs_flag"] = "STRONG_RS"
                results.append(row_dict)

        if not results:
            out = df.copy()
            out["rs_rating"] = None
            out["rs_score"] = None
            out["rs_status"] = "DATA_MISSING"
            out["rs_flag"] = "DATA_MISSING"
            out["rs_warning"] = "RS 결과 없음"
            return out

        return pd.DataFrame(results)

