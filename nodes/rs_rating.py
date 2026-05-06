"""
상대 강도(RS) 평가 노드.
"""
import pandas as pd
import logging
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from engine.leakage_guard import assert_no_future_data
from data.holidays import prev_trading_day
from backend.algo_settings import algo_settings

logger = logging.getLogger(__name__)

class RsRatingParams(BaseModel):
    lookback_days: int = 252

class RsRatingNode(BaseNode):
    NODE_TYPE      = "rs_rating"
    DISPLAY_NAME   = "상대 강도(RS) 평가"
    DESCRIPTION    = "KOSPI 대비 최근 52주 초과 수익률 백분위 평가."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("rs_rating", "rs_score")
    ParamsModel    = RsRatingParams

    def run(self, inputs: list[pd.DataFrame], params: RsRatingParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0]
        if df.empty or not context.krx_client:
            return df

        as_of = context.as_of_date
        start_date = prev_trading_day(as_of, n=params.lookback_days)

        # 1. 상위 100대 종목(시총 기준) + 059500 KOSPI ETF를 벤치마크로 사용
        #    500→100 축소: 네트워크 부하 감소, 충분한 통계적 유의성 확보
        universe = context.krx_client.get_universe(as_of)
        if universe.empty:
            df["rs_rating"] = None
            df["rs_score"] = 0
            return df

        benchmark_codes = universe.sort_values("market_cap", ascending=False).head(100)["code"].tolist()
        target_codes = df["code"].tolist()
        kospi_etf = "069500"
        all_codes = list(set(benchmark_codes + target_codes + [kospi_etf]))

        # 2. 단일 기간 페치 (start_date ~ as_of) — 기존 2회 단일날짜 페치 대비 절반 API 호출
        #    start_date가 휴장일이어도 기간 범위 내 첫 거래일을 자동으로 사용함
        logger.info("RS 계산: %d종목 기간 데이터 수집 (%s ~ %s)", len(all_codes), start_date, as_of)
        period_ohlcv = context.krx_client.get_ohlcv_batch(all_codes, start_date=start_date, end_date=as_of)

        # 3. 코드별 기간 수익률 계산 (첫 거래일 → 마지막 거래일)
        raw_returns: dict[str, float] = {}
        for code, hist in period_ohlcv.items():
            if hist.empty or len(hist) < 2:
                continue
            c_start = hist["close"].iloc[0]
            c_end   = hist["close"].iloc[-1]
            if c_start > 0:
                raw_returns[code] = c_end / c_start - 1.0

        if not raw_returns:
            logger.warning("RS: 수익률 데이터 수집 실패 — 모든 종목 rs_rating=None 처리")
            df["rs_rating"] = None
            df["rs_score"]  = 0
            return df

        # 4. KOSPI 초과 수익률로 변환 후 전 종목 백분위 산출 (신뢰도 89% 버전 복구)
        kospi_ret = raw_returns.get(kospi_etf, 0.0)
        excess = {code: ret - kospi_ret for code, ret in raw_returns.items()}
        all_rs_ratings = pd.Series(excess).rank(pct=True) * 100.0
        
        # 시총 정보는 레이블용으로만 유지
        universe_info = dict(zip(universe["code"], universe["market_cap"]))

        # 5. 입력 종목 매핑 및 min_rating 필터
        min_rating = algo_settings.rs_min_rating
        results = []

        for _, row in df.iterrows():
            code   = row["code"]
            rating = all_rs_ratings.get(code)  # None이면 데이터 미수집

            row_dict = row.to_dict()

            if rating is None:
                # 데이터 미수집 — 종목 유지, 점수 0 (실제 0과 구별 가능하도록 None 저장)
                row_dict["rs_rating"] = None
                row_dict["rs_score"]  = 0
                results.append(row_dict)
            elif rating < min_rating:
                continue  # RS 기준 미달 → 제거
            else:
                row_dict["rs_rating"] = round(rating, 1)
                row_dict["rs_score"]  = 50 if rating >= 90 else 35
                results.append(row_dict)

        if not results:
            return df.head(0)

        return pd.DataFrame(results)
