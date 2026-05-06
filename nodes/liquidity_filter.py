"""
유동성 필터 노드.
일평균 거래대금이 최소 기준 이하인 종목을 제거합니다.

코스닥 소형주는 호가창 공백으로 슬리피지가 과도해질 수 있으므로,
Universe 하위에 반드시 적용하는 것을 권장합니다.
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from engine.leakage_guard import assert_no_future_data
from data.holidays import prev_trading_day


class LiquidityFilterParams(BaseModel):
    min_trading_value_krw: float = 3_000_000_000  # 30억 (일평균 거래대금 하한)
    min_market_cap_krw: float = 50_000_000_000    # 500억 (시가총액 하한)
    lookback_days: int = 20                        # 평균 산출 기간 (거래일)


class LiquidityFilterNode(BaseNode):
    NODE_TYPE      = "liquidity_filter"
    DISPLAY_NAME   = "유동성 필터"
    DESCRIPTION    = "일평균 거래대금 기준 유동성 부족 종목을 제거합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("avg_trading_value",)
    ParamsModel    = LiquidityFilterParams

    def run(
        self,
        inputs: list[pd.DataFrame],
        params: LiquidityFilterParams,
        context: ExecutionContext,
    ) -> pd.DataFrame:
        df = inputs[0]
        if df.empty or not context.krx_client:
            return df

        start_date = prev_trading_day(context.as_of_date, n=params.lookback_days + 10)
        codes = df["code"].tolist()

        ohlcv_dict = context.krx_client.get_ohlcv_batch(codes, start_date, context.as_of_date)

        results = []
        for _, row in df.iterrows():
            code = row["code"]

            # 시가총액 하한 필터 (API 데이터 기준, 추가 네트워크 호출 없음)
            market_cap = row.get("market_cap", 0) or 0
            
            hist = ohlcv_dict.get(code)
            if hist is None or hist.empty or len(hist) < 5:
                if context.is_single_analysis:
                    row_dict = row.to_dict()
                    row_dict["avg_trading_value"] = 0
                    row_dict["liquidity_warning"] = "❌ 데이터 부족"
                    results.append(row_dict)
                continue

            # Look-ahead 방지
            assert_no_future_data(hist, context.as_of_date, context=f"LiquidityFilterNode:{code}")

            # 최근 lookback_days의 일평균 거래대금 = close × volume
            recent = hist.tail(params.lookback_days)
            trading_values = recent["close"] * recent["volume"]
            avg_value = trading_values.mean()

            # [개선] 정밀 분석 모드면 통과 여부 상관없이 데이터 포함
            is_passed_cap = (params.min_market_cap_krw <= 0 or market_cap >= params.min_market_cap_krw)
            is_passed_val = (avg_value >= params.min_trading_value_krw)

            if context.is_single_analysis or (is_passed_cap and is_passed_val):
                row_dict = row.to_dict()
                row_dict["avg_trading_value"] = int(avg_value)
                if not is_passed_cap or not is_passed_val:
                    row_dict["liquidity_warning"] = "⚠️ 유동성 부족 (주의)"
                results.append(row_dict)

        if not results:
            empty = df.head(0).copy()
            empty["avg_trading_value"] = pd.Series(dtype=float)
            return empty

        return pd.DataFrame(results)
