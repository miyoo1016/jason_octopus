"""
VCP(Volatility Contraction Pattern) 패턴 찾기 노드.
"""
import pandas as pd
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext
from engine.leakage_guard import assert_no_future_data
from data.holidays import prev_trading_day
from backend.algo_settings import algo_settings

class VcpParams(BaseModel):
    lookback_days: int = 120

class VcpNode(BaseNode):
    NODE_TYPE      = "vcp"
    DISPLAY_NAME   = "VCP 패턴 찾기"
    DESCRIPTION    = "변동성이 수축하는 VCP 패턴 형태 종목 필터."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("vcp_score",)
    ParamsModel    = VcpParams

    def run(self, inputs: list[pd.DataFrame], params: VcpParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0]
        if df.empty or not context.krx_client:
            return df
            
        lookback = algo_settings.vcp_lookback_days
        pivot_w = algo_settings.vcp_pivot_window
        max_depth = algo_settings.vcp_max_depth_pct
        min_score = algo_settings.vcp_min_score

        start_date = prev_trading_day(context.as_of_date, n=lookback + 60)
        codes = df["code"].tolist()
        
        ohlcv_dict = context.krx_client.get_ohlcv_batch(codes, start_date, context.as_of_date)

        results = []
        for _, row in df.iterrows():
            code = row["code"]
            hist = ohlcv_dict.get(code)
            if hist is None or hist.empty or len(hist) < 20:
                continue
            assert_no_future_data(hist, context.as_of_date, context=f"VcpNode:{code}")
                
            # Use only the lookback period for VCP calculation
            if len(hist) > lookback:
                vcp_hist = hist.iloc[-lookback:]
            else:
                vcp_hist = hist
                
            highs = vcp_hist["high"].values
            lows = vcp_hist["low"].values
            volumes = vcp_hist["volume"].values
            n = len(vcp_hist)
            
            if n < pivot_w * 2 + 1:
                continue
                
            # 1. Identify Pivot Highs
            pivot_indices = []
            for i in range(pivot_w, n - pivot_w):
                window_highs = highs[i - pivot_w : i + pivot_w + 1]
                if highs[i] == max(window_highs):
                    # Handle flat tops
                    is_first = True
                    for j in range(i - pivot_w, i):
                        if highs[j] == highs[i]:
                            is_first = False
                            break
                    if is_first:
                        pivot_indices.append(i)
                        
            num_pivots = len(pivot_indices)
            if num_pivots < 3:
                continue
                
            # 2. Calculate Contraction Depth
            contractions = []
            for k in range(num_pivots - 1):
                idx_start = pivot_indices[k]
                idx_end = pivot_indices[k+1]
                prev_high = highs[idx_start]
                period_low = min(lows[idx_start:idx_end+1])
                depth = (prev_high - period_low) / prev_high * 100
                contractions.append(depth)
                
            last_pivot_idx = pivot_indices[-1]
            last_high = highs[last_pivot_idx]
            final_low = min(lows[last_pivot_idx:])
            final_depth = (last_high - final_low) / last_high * 100
            
            all_contractions = contractions + [final_depth]
            
            if max(all_contractions) > max_depth:
                continue
                
            # 1. 스윙 탐지 기간 제한 (최근 65거래일, 약 3개월로 제한하여 과거 데이터 왜곡 방지)
            vcp_hist = hist.tail(65).copy()
            
            # [개선] 25.0% 이상의 낙폭은 VCP 수축이 아닌 '이벤트 충격'으로 분류하여 제외
            valid_contractions = [d for d in all_contractions if d < 25.0]
            event_shocks = [d for d in all_contractions if d >= 25.0]
            num_contractions = len(valid_contractions)
            
            if num_contractions < 2: # 최소 수축 횟수 미달 시 스킵
                continue
                
            depth_str = "->".join([f"{d:.1f}%" for d in valid_contractions])
            vcp_info = f"수축 {num_contractions}회: {depth_str}"
            
            # [추가] 분리된 이벤트 충격 표시
            if event_shocks:
                shock_str = ", ".join([f"{s:.1f}%" for s in event_shocks])
                vcp_info += f" (⚠️ 이벤트 충격 {shock_str} 분리됨)"

            # [개선] 역수축 판정: 3회 연속 증가할 때만 경고
            is_reverse = False
            if len(valid_contractions) >= 3:
                increments = 0
                for k in range(1, len(valid_contractions)):
                    if valid_contractions[k] > valid_contractions[k-1]:
                        increments += 1
                    else:
                        increments = 0 # 연속성 깨짐
                if increments >= 2: # 3회 연속 증가 (2번의 상승)
                    is_reverse = True
            
            # 4. 점수 산출 및 주도주 우대
            rs_val = row.get("rs_rating", 0)
            market_cap = row.get("market_cap", 0)
            
            score = 70
            if num_contractions >= 3: score = 85
            if num_contractions >= 4: score = 95
            
            # [개선] 시총 상위 20위 또는 RS 90 이상 주도주 면죄부
            if rs_val >= 90 or (market_cap > 10_000_000_000_000): # 시총 10조 이상
                score = 100
                vcp_info = f"🔥 주도주 패턴 완성! | {vcp_info}"
                is_reverse = False
            
            warning = None
            if len(hist) >= 60:
                # [개선] 오늘의 폭발적 거래량을 수축기 평균에서 제외 (iloc[-21:-1])
                vol_contraction_area = hist["volume"].iloc[-21:-1].mean()
                vol_base = hist["volume"].iloc[-61:-1].mean()
                vol_pct = (vol_contraction_area / vol_base * 100) if vol_base > 0 else 100
                
                if vol_pct <= 75: # 기준 소폭 완화
                    if score < 100: score += 10
                    warning = f"거래량 급감 확인 ({vol_pct:.1f}%)"
                else:
                    if score < 100: score -= 10
                    warning = f"⚠️ 수축기 거래량 미감소 ({vol_pct:.1f}%)"
            
            row_dict = row.to_dict()
            row_dict["vcp_score"] = min(100, score)
            
            # 정보 통합
            full_info = f"{vcp_info} | {warning}" if warning else vcp_info
            if is_reverse:
                full_info = f"⚠️ 역수축 경고! {full_info}"
                
            row_dict["vcp_warning"] = full_info
            results.append(row_dict)
                
        if not results:
            empty = df.head(0).copy()
            empty["vcp_score"] = pd.Series(dtype=float)
            empty["vcp_warning"] = pd.Series(dtype=object)
            return empty
        return pd.DataFrame(results)
