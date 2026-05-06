"""
박스권 돌파 찾기 노드.
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from engine.leakage_guard import assert_no_future_data
from data.holidays import prev_trading_day

class BoxBreakoutParams(BaseModel):
    box_period: int = 60
    breakout_pct: float = 1.0

class BoxBreakoutNode(BaseNode):
    NODE_TYPE      = "box_breakout"
    DISPLAY_NAME   = "박스권 돌파 찾기"
    DESCRIPTION    = "최근 N일 최고가 근접 또는 돌파 종목 필터."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("box_breakout_pct", "box_breakout_grade")
    ParamsModel    = BoxBreakoutParams

    def run(self, inputs: list[pd.DataFrame], params: BoxBreakoutParams, context: ExecutionContext) -> pd.DataFrame:
        from backend.algo_settings import algo_settings
        df = inputs[0]
        if df.empty or not context.krx_client:
            return df
            
        start_date = prev_trading_day(context.as_of_date, n=params.box_period + 30)
        codes = df["code"].tolist()
        
        ohlcv_dict = context.krx_client.get_ohlcv_batch(codes, start_date, context.as_of_date)
        
        results = []
        for _, row in df.iterrows():
            code = row["code"]
            hist = ohlcv_dict.get(code)
            if hist is None or hist.empty or len(hist) < params.box_period:
                continue
            assert_no_future_data(hist, context.as_of_date, context=f"BoxBreakoutNode:{code}")

            # 마지막 거래일 종가 등
            curr = hist.iloc[-1]
            curr_close = curr["close"]
            curr_open = curr["open"]
            curr_high = curr["high"]
            curr_vol = curr["volume"]
            
            # 최근 20일 평균 거래량 (당일 제외)
            if len(hist) < 21:
                continue
            avg_vol_20 = hist["volume"].iloc[-21:-1].mean()
            
            # 마지막 거래일 이전 box_period 동안의 고점
            recent_hist = hist.iloc[-params.box_period-1:-1]
            if recent_hist.empty:
                continue
            box_high = recent_hist["high"].max()
            
            if box_high > 0:
                breakout_pct = ((curr_close / box_high) - 1.0) * 100
                is_passed = breakout_pct >= params.breakout_pct
                
                # Grade evaluation
                is_yangbong = curr_close > curr_open
                high_pos = curr_close >= curr_high * 0.95
                res_break = curr_close >= box_high * 1.01
                vol_ratio = curr_vol / avg_vol_20 if avg_vol_20 > 0 else 0
                
                grade = "D"
                warning = None
                
                if vol_ratio >= 1.5 and is_yangbong and high_pos and res_break:
                    grade = "A"
                elif vol_ratio >= 1.2 and is_yangbong:
                    grade = "B"
                elif vol_ratio >= 1.0:
                    grade = "C"
                    warning = f"⚠️ 거래량 미확인 ({vol_ratio:.1f}배)"
                else:
                    grade = "D"
                    warning = f"⚠️ 거래량 부족 ({vol_ratio:.1f}배)"
                    
                # [개선] 정밀 분석 모드면 통과 여부 상관없이 데이터 포함
                if not context.is_single_analysis:
                    if not is_passed or grade == "D":
                        continue
                        
                row_dict = row.to_dict()
                row_dict["box_breakout_pct"] = breakout_pct
                row_dict["box_breakout_grade"] = f"{grade} ({vol_ratio:.1f}배)"
                if not is_passed:
                    row_dict["box_breakout_warning"] = f"❌ 박스권 미돌파 ({breakout_pct:.1f}%)"
                elif warning:
                    row_dict["box_breakout_warning"] = warning
                results.append(row_dict)
                    
        if not results:
            empty = df.head(0).copy()
            empty["box_breakout_pct"] = pd.Series(dtype=float)
            empty["box_breakout_grade"] = pd.Series(dtype=object)
            empty["box_breakout_warning"] = pd.Series(dtype=object)
            return empty
        return pd.DataFrame(results)
