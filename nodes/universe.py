"""
국장 종목 유니버스 노드.
"""
from typing import Literal
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext

class UniverseParams(BaseModel):
    market: Literal["ALL", "KOSPI", "KOSDAQ"] = "ALL"
    manual_codes: list[str] = None  # 특정 종목만 분석할 때 사용
    max_symbols: int | None = None

class UniverseNode(BaseNode):
    NODE_TYPE      = "universe"
    DISPLAY_NAME   = "국장 종목"
    DESCRIPTION    = "KOSPI / KOSDAQ 전 종목을 불러옵니다."
    INPUT_ARITY    = 0
    OUTPUT_COLUMNS = ("market_cap",)
    ParamsModel    = UniverseParams

    def run(self, inputs: list[pd.DataFrame], params: UniverseParams, context: ExecutionContext) -> pd.DataFrame:
        if not context.krx_client:
            raise RuntimeError("krx_client가 제공되지 않았습니다.")

        # [최적화] manual_codes가 있고 1개뿐인 정밀 분석 모드면 시장 전체 페치 대신 조기 종료 활용
        df = context.krx_client.get_universe(context.as_of_date, market=params.market, manual_codes=params.manual_codes)

        # 특정 종목 코드만 요청된 경우 필터링
        if params.manual_codes:
            df_filtered = df[df["code"].isin(params.manual_codes)].reset_index(drop=True)

            # [수정] 만약 유니버스 페치에서 누락되었으나 정밀 분석 모드라면 강제로 1행 생성 (데이터 유실 방지)
            if df_filtered.empty and context.is_single_analysis:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"UniverseNode: {params.manual_codes}를 유니버스에서 찾을 수 없어 강제 생성 시도")

                rows = []
                for code in params.manual_codes:
                    # OHLCV에서 현재가라도 가져옴
                    hist = context.krx_client.get_ohlcv(code, end_date=context.as_of_date, pages=1)
                    if not hist.empty:
                        last = hist.iloc[-1]
                        rows.append({
                            "code": code,
                            "name": f"Unknown({code})", # 이름은 알 수 없으나 분석은 진행
                            "market": "ALL",
                            "close": int(last["close"]),
                            "volume": int(last["volume"]),
                            "market_cap": 0 # 시총 정보 없음
                        })
                if rows:
                    df_filtered = pd.DataFrame(rows)

            df = df_filtered

        # [Symbol Trace] 효성중공업(298040) 추적용 로직
        trace_id = "298040"
        trace_data = {"symbol": trace_id, "found_in_universe": False, "dropped_by_max_symbols": False}
        
        if trace_id in df["code"].values:
            trace_data["found_in_universe"] = True
            
        if params.max_symbols and params.max_symbols > 0:
            if len(df) > params.max_symbols:
                top_symbols = df.head(params.max_symbols)["code"].values
                if trace_id in df["code"].values and trace_id not in top_symbols:
                    trace_data["dropped_by_max_symbols"] = True
            df = df.head(params.max_symbols).reset_index(drop=True)

        df.attrs["hyosung_trace"] = trace_data
        return df
