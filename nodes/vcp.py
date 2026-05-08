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
    min_score: int = 70  # UI에서 동적으로 조절 가능하도록 추가

class VcpNode(BaseNode):
    NODE_TYPE      = "vcp"
    DISPLAY_NAME   = "VCP 패턴 찾기"
    DESCRIPTION    = "변동성이 수축하는 VCP 패턴 형태 종목 필터."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("vcp_score", "change_pct")
    ParamsModel    = VcpParams

    def run(self, inputs: list[pd.DataFrame], params: VcpParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0]
        if df.empty or not context.krx_client:
            return df
            
        lookback = params.lookback_days
        pivot_w = algo_settings.vcp_pivot_window
        max_depth = algo_settings.vcp_max_depth_pct
        min_score = params.min_score

        start_date = prev_trading_day(context.as_of_date, n=lookback + 60)
        # [안전장치] VCP 계산 상위 200개 종목 제한
        codes = df["code"].tolist()[:200]
        
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
                if context.is_single_analysis:
                    row_dict = row.to_dict()
                    row_dict["vcp_score"] = 0
                    row_dict["vcp_warning"] = "❌ VCP 패턴 미형성 (피벗 부족)"
                    results.append(row_dict)
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
                if context.is_single_analysis:
                    row_dict = row.to_dict()
                    row_dict["vcp_score"] = 0
                    row_dict["vcp_warning"] = f"❌ 변동성 과다 ({max(all_contractions):.1f}% > {max_depth}%)"
                    results.append(row_dict)
                continue
                
            # 1. 스윙 탐지 기간 제한 (최근 65거래일, 약 3개월로 제한하여 과거 데이터 왜곡 방지)
            vcp_hist = hist.tail(65).copy()
            
            # [개선] 25.0% 이상의 낙폭은 VCP 수축이 아닌 '이벤트 충격'으로 분류하여 제외
            valid_contractions = [d for d in all_contractions if d < 25.0]
            event_shocks = [d for d in all_contractions if d >= 25.0]
            num_contractions = len(valid_contractions)
            
            if num_contractions < 2: # 최소 수축 횟수 미달 시 스킵
                if context.is_single_analysis:
                    row_dict = row.to_dict()
                    row_dict["vcp_score"] = 50 if num_contractions == 1 else 0
                    row_dict["vcp_warning"] = f"⚠️ 수축 횟수 미달 ({num_contractions}회)"
                    results.append(row_dict)
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
            
            warnings = []
            if len(hist) >= 60:
                # [개선] 오늘의 폭발적 거래량을 수축기 평균에서 제외 (iloc[-21:-1])
                vol_contraction_area = hist["volume"].iloc[-21:-1].mean()
                vol_base = hist["volume"].iloc[-61:-1].mean()
                vol_pct = (vol_contraction_area / vol_base * 100) if vol_base > 0 else 100
                
                # [개선] 거래량 수축 판정 임계값 완화 (75% -> 90%)
                if vol_pct <= 90:
                    if score < 100: score += 10
                    warnings.append(f"거래량 급감 확인 ({vol_pct:.1f}%)")
                else:
                    # 주도주는 거래량이 조금 있어도 매집으로 간주 (면죄부)
                    if rs_val >= 90 or market_cap > 10_000_000_000_000:
                        pass
                    else:
                        if score < 100: score -= 10
                        warnings.append(f"⚠️ 수축기 거래량 미감소 ({vol_pct:.1f}%)")

            latest = hist.iloc[-1] if len(hist) else None
            if latest is not None:
                today_high = float(latest.get("high", 0) or 0)
                today_close = float(latest.get("close", 0) or 0)
                if today_high > 0:
                    close_retreat = (today_high - today_close) / today_high
                    if close_retreat >= 0.03:
                        score -= 10
                        warnings.append(f"⚠️ 장중 고점 대비 종가 -{close_retreat * 100:.1f}% 후퇴")

                if len(hist) >= 21:
                    prev_close = float(hist["close"].iloc[-2] or 0) if len(hist) >= 2 else 0
                    avg_volume = float(hist["volume"].iloc[-21:-1].mean() or 0)
                    today_volume = float(latest.get("volume", 0) or 0)
                    change_pct = ((today_close / prev_close) - 1.0) if prev_close > 0 else 0
                    if change_pct >= 0.10 and avg_volume > 0 and today_volume >= avg_volume * 3:
                        warnings.append("⚡ 급등 + 거래량 폭증")
                    elif change_pct <= -0.10 and avg_volume > 0 and today_volume >= avg_volume * 3:
                        warnings.append("⚠️ 급락 + 거래량 폭증")
            else:
                change_pct = 0.0

            # [추가] 급등 피로도(Exhaustion) 체크
            # 최근 120일 저점 대비 현재가가 너무 높으면 '피로도' 경고 추가
            if len(hist) >= 60:
                low_120 = hist["low"].iloc[-120:].min() if len(hist) >= 120 else hist["low"].min()
                rally_ratio = (today_close / low_120 - 1.0) * 100
                if rally_ratio > 100: # 120일 내 100% 이상 급등 시
                    warnings.append(f"⚠️ 급등 피로도 ({rally_ratio:.0f}%↑)")
                    if score >= 100: score -= 10 # Tier 1 방어 (무분별한 추격 매수 방지)

            row_dict = row.to_dict()
            row_dict["vcp_score"]   = max(0, min(100, score))
            row_dict["change_pct"]  = round(change_pct, 4)   # 당일 등락률 (소수, 0.15 = +15%)
            
            # 정보 통합
            warning = " | ".join(warnings)
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
