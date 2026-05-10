"""
유동성 필터 노드.
일평균 거래대금이 최소 기준 이하인 종목을 제거합니다.

코스닥 소형주는 호가창 공백으로 슬리피지가 과도해질 수 있으므로,
Universe 하위에 반드시 적용하는 것을 권장합니다.
"""
from typing import Any
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from engine.leakage_guard import assert_no_future_data
from data.holidays import prev_trading_day


def parse_volume(val: Any) -> float | None:
    """거래량 문자열 파싱 (K, M, B, 쉼표, 공백, 한글 단위 대응).

    파싱 실패 시 0이 아니라 None을 반환하여 DATA_MISSING/UNKNOWN 경로로 흘려보낸다.

    지원 입력:
        "522.04K" -> 522040.0
        "5.00M"   -> 5000000.0
        "522,040" -> 522040.0
        "522.04천" -> 522040.0
        "1.2백만"  -> 1200000.0
        "3.4억"    -> 340000000.0
        "1.5B"    -> 1500000000.0
        숫자/None/""/-/"N/A"
    """
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "—", "N/A", "n/a", "NA", "null", "None"):
        return None

    try:
        # 한글 단위 (배수 큰 순서로 매칭)
        if "억" in s:
            return float(s.replace("억", "")) * 100_000_000
        if "백만" in s:
            return float(s.replace("백만", "")) * 1_000_000
        if "만" in s:
            return float(s.replace("만", "")) * 10_000
        if "천" in s:
            return float(s.replace("천", "")) * 1_000

        # 영문 단위 (대소문자 구분 없음)
        upper = s.upper()
        if upper.endswith("B"):
            return float(s[:-1]) * 1_000_000_000
        if upper.endswith("M"):
            return float(s[:-1]) * 1_000_000
        if upper.endswith("K"):
            return float(s[:-1]) * 1_000

        return float(s)
    except (ValueError, TypeError):
        return None


class LiquidityFilterParams(BaseModel):
    min_trading_value_krw: float = 2_000_000_000  # 20억 (최소 일평균 거래대금 하한)
    min_market_cap_krw: float = 50_000_000_000    # 500억 (시가총액 하한)
    lookback_days: int = 20                        # 평균 산출 기간 (거래일)


class LiquidityFilterNode(BaseNode):
    NODE_TYPE      = "liquidity_filter"
    CACHE_VERSION  = "liquidity-refined-v1"
    DISPLAY_NAME   = "유동성 필터"
    DESCRIPTION    = "거래대금(종가*거래량) 및 원천 데이터를 교차 검증하여 유동성 상태를 판정합니다."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = (
        "liquidity_price", "liquidity_close", "liquidity_volume", "raw_trading_value",
        "calculated_trading_value", "liquidity_trading_value", "liquidity_trading_value_source",
        "liquidity_avg_trading_value", "liquidity_threshold", "liquidity_status",
        "liquidity_reason", "liquidity_data_warning", "avg_trading_value",
        "volume_suspicious", "liquidity_volume_source", "liquidity_close_source",
        "liquidity_volume_raw", "liquidity_quote_source",
        "trading_value", "trading_value_source", "volume_source", "market_cap_unit", "data_unit_check"
    )
    ParamsModel    = LiquidityFilterParams

    def run(
        self,
        inputs: list[pd.DataFrame],
        params: LiquidityFilterParams,
        context: ExecutionContext,
    ) -> pd.DataFrame:
        df = inputs[0].copy()
        if df.empty:
            return df

        # 진단용 보관
        suspicious_records = []

        start_date = prev_trading_day(context.as_of_date, n=params.lookback_days + 5)
        codes = df["code"].tolist()

        # OHLCV 일봉 데이터 (가장 신뢰할 수 있는 소스)
        ohlcv_dict = context.krx_client.get_ohlcv_batch(codes, start_date, context.as_of_date) if context.krx_client else {}

        results = []
        for _, row in df.iterrows():
            code = row["code"]
            name = row.get("name", "")
            mkt_cap = float(row.get("market_cap", 0)) if not pd.isna(row.get("market_cap")) else 0.0

            # 1. 데이터 수집 및 파싱
            hist = ohlcv_dict.get(code)

            # 원천 데이터 (row에 이미 있을 수 있는 값)
            raw_vol = parse_volume(row.get("volume"))
            raw_val = row.get("trading_value") or row.get("raw_trading_value")
            if pd.isna(raw_val): raw_val = None
            else: raw_val = float(raw_val)

            # 현재 시점 데이터 (Primary: OHLCV hist)
            curr_close = None
            curr_vol = None
            vol_source = "none"
            close_source = "none"

            if hist is not None and not hist.empty:
                assert_no_future_data(hist, context.as_of_date, context=f"LiquidityFilterNode:{code}")
                last_row = hist.iloc[-1]
                curr_close = float(last_row["close"])
                curr_vol = float(last_row["volume"])
                vol_source = "ohlcv_hist"
                close_source = "ohlcv_hist"

            # Fallback 1: hist가 없으면 row의 volume 사용 (0 제외 — universe _parse_int 기본값)
            if curr_vol is None and raw_vol is not None and raw_vol > 0:
                curr_vol = raw_vol
                vol_source = "universe_row"
            elif curr_vol is None and raw_vol is not None:
                # volume=0 은 API 결측 가능성 → vol_source 명시하되 0으로 기록
                curr_vol = raw_vol
                vol_source = "universe_row_zero"

            # Fallback 2: hist가 없으면 row의 close 사용 (canonical snapshot)
            if curr_close is None:
                row_close = row.get("close")
                if row_close is not None and not pd.isna(row_close) and float(row_close) > 0:
                    curr_close = float(row_close)
                    close_source = "universe_row"

            # 2. Volume Suspicious 감지 로직
            is_suspicious = False
            suspicious_reason = ""
            if curr_vol is not None and curr_close is not None:
                # 시총 1조+ & 1만주 미만
                if mkt_cap >= 1_000_000_000_000 and curr_vol < 10_000:
                    is_suspicious = True
                    suspicious_reason = f"대형주(1조+) 저거래량({curr_vol:,.0f}주)"
                # 시총 10조+ & 5만주 미만
                elif mkt_cap >= 10_000_000_000_000 and curr_vol < 50_000:
                    is_suspicious = True
                    suspicious_reason = f"초대형주(10조+) 저거래량({curr_vol:,.0f}주)"
                # 고가주(10만+) & 1천주 미만
                elif curr_close >= 100_000 and curr_vol < 1_000:
                    is_suspicious = True
                    suspicious_reason = f"고가주(10만+) 저거래량({curr_vol:,.0f}주)"
                # 시총 10조+인데 거래대금 20억 미만
                elif mkt_cap >= 10_000_000_000_000 and (curr_close * curr_vol) < 2_000_000_000:
                    is_suspicious = True
                    suspicious_reason = f"초대형주 저거래대금({(curr_close * curr_vol)/1e8:.1f}억)"

            # 3. Fallback Pipeline (Suspicious인 경우)
            attempted_fallbacks = []
            if is_suspicious:
                # Fallback A: Quote volume 시도 (있는 경우)
                quote_vol = parse_volume(row.get("quote_volume"))
                if quote_vol is not None and quote_vol > curr_vol * 10:
                    attempted_fallbacks.append("quote_volume")
                    curr_vol = quote_vol
                    vol_source = "quote_volume_fallback"
                    is_suspicious = False # 해소된 것으로 간주 (일단)

                # Fallback B: raw_trading_value 기반 역산 또는 직접 사용
                if is_suspicious and raw_val is not None and raw_val >= 2_000_000_000:
                    attempted_fallbacks.append("raw_trading_value_trust")
                    # 거래대금이 확실히 크면 volume suspicious 무시하고 진행하거나 status 변경

            # 4. 거래대금 계산 및 최종 소스 결정
            calculated_val = None
            if pd.notna(curr_close) and pd.notna(curr_vol):
                calculated_val = curr_close * curr_vol

            final_val = 0.0
            val_source = "missing"
            warning = None
            status = "LIQUIDITY_UNKNOWN"
            reason = ""

            if pd.notna(calculated_val):
                final_val = float(calculated_val)
                val_source = "calculated_close_x_volume"

                if is_suspicious:
                    # 여전히 의심스러운데 거래대금은 기준 미달인 경우 -> ILLIQUID 확정 금지
                    if final_val < params.min_trading_value_krw:
                        status = "LIQUIDITY_UNCERTAIN"
                        reason = f"유동성 데이터 불확실: {suspicious_reason}"
                    else:
                        status = "LIQUID" # 기준은 넘었으므로 일단 통과시키되 경고
                        warning = f"거래량 의심({suspicious_reason})"
                else:
                    # 정상 판정
                    if final_val >= params.min_trading_value_krw:
                        status = "LIQUID"
                        reason = "거래대금 기준 통과"
                    else:
                        status = "ILLIQUID"
                        reason = f"거래대금 기준 미달 ({final_val:,.0f} < {params.min_trading_value_krw:,.0f})"

            elif pd.notna(raw_val):
                final_val = raw_val
                val_source = "raw_trading_value_fallback"
                if final_val >= params.min_trading_value_krw:
                    status = "LIQUIDITY_FALLBACK"
                    reason = "원천 거래대금으로 유동성 확인 (거래량 누락)"
                else:
                    status = "ILLIQUID"
                    reason = "원천 거래대금 기준 미달"
            else:
                status = "DATA_MISSING"
                reason = "유동성 데이터(거래량/거래대금) 모두 누락"

            if is_suspicious:
                suspicious_records.append({
                    "symbol": code, "name": name, "close": curr_close, "volume": curr_vol,
                    "market_cap": mkt_cap, "calculated_trading_value": calculated_val,
                    "threshold": params.min_trading_value_krw, "reason": suspicious_reason,
                    "attempted_fallbacks": attempted_fallbacks, "final_liquidity_status": status
                })

            # 평균 거래대금 (보조)
            avg_val = 0.0
            if hist is not None and not hist.empty:
                recent = hist.tail(params.lookback_days)
                avg_val = (recent["close"] * recent["volume"]).mean()

            # 결과 조립
            row_dict = row.to_dict()
            # quote_source: 데이터 소스 통합 요약
            if close_source == "ohlcv_hist" and vol_source == "ohlcv_hist":
                quote_source = "ohlcv_full"
            elif close_source == "universe_row" and vol_source in ("universe_row", "universe_row_zero"):
                quote_source = "universe_snapshot"
            elif close_source == "ohlcv_hist" and vol_source in ("universe_row", "universe_row_zero"):
                quote_source = "ohlcv_close_row_vol"
            elif close_source == "universe_row" and vol_source == "ohlcv_hist":
                quote_source = "row_close_ohlcv_vol"
            else:
                quote_source = "none"

            row_dict.update({
                "liquidity_price": curr_close,
                "liquidity_close": curr_close,
                "liquidity_volume": curr_vol,
                "liquidity_volume_raw": row.get("volume"),
                "liquidity_volume_source": vol_source,
                "liquidity_close_source": close_source,
                "liquidity_quote_source": quote_source,
                "volume_suspicious": is_suspicious,
                "raw_trading_value": raw_val,
                "calculated_trading_value": calculated_val,
                "liquidity_trading_value": final_val,
                "liquidity_trading_value_source": val_source,
                "trading_value": final_val,
                "trading_value_source": val_source,
                "volume_source": vol_source,
                "market_cap_unit": "KRW",
                "data_unit_check": "DATA_UNIT_WARNING" if is_suspicious else "OK",
                "liquidity_avg_trading_value": avg_val,
                "liquidity_threshold": params.min_trading_value_krw,
                "liquidity_status": status,
                "liquidity_reason": reason,
                "liquidity_data_warning": warning,
                "avg_trading_value": int(avg_val) if pd.notna(avg_val) else 0,
                "liquidity_score": 100 if status == "LIQUID" else (70 if status in {"LIQUIDITY_FALLBACK", "LIQUIDITY_UNVERIFIED"} else 30),
                "liquidity_flag": status,
                "liquidity_warning": warning or reason
            })
            results.append(row_dict)

        out_df = pd.DataFrame(results)
        out_df.attrs["suspicious_liquidity_records"] = suspicious_records
        return out_df
