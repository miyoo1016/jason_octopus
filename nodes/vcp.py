"""
VCP(Volatility Contraction Pattern) 분석 노드.

이 노드는 더 이상 정석 VCP 미충족 종목을 제거하지 않습니다. 가격 데이터 자체가
부족한 경우에도 DATA_MISSING 플래그와 중립 점수를 붙여 뒤 scoring layer가
최종 판단하게 합니다.
"""
import pandas as pd
from pydantic import BaseModel

from backend.algo_settings import algo_settings
from backend.alphaforge_config import config_section
from data.holidays import prev_trading_day
from engine.leakage_guard import assert_no_future_data
from engine.node_base import BaseNode, ExecutionContext


class VcpParams(BaseModel):
    lookback_days: int = 120
    min_score: int = 70


class VcpNode(BaseNode):
    NODE_TYPE = "vcp"
    CACHE_VERSION = "strict-quality-v2"
    DISPLAY_NAME = "VCP 패턴 찾기"
    DESCRIPTION = "변동성 수축 상태를 점수와 상태 플래그로 분류합니다."
    INPUT_ARITY = 1
    OUTPUT_COLUMNS = (
        "vcp_raw_score", "vcp_effective_score", "vcp_display_score", "vcp_score",
        "change_pct", "vcp_status", "vcp_flag", "adjusted_box_limit", "stock_atr_multiplier",
        "vcp_data_rows", "vcp_width_trend", "vcp_contraction_count", "vcp_last_base_width_pct",
        "vcp_prev_base_width_pct", "vcp_atr_trend", "vcp_volume_dryup_score", "vcp_volume_trend",
        "vcp_price_tightness_score", "vcp_reverse_expansion_flag", "vcp_rally_exhaustion_flag",
        "vcp_reason_codes", "vcp_confidence", "vcp_cross_warning",
        "vcp_width_score", "vcp_atr_score", "vcp_penalty_reasons", "vcp_bonus_reasons"
    )
    ParamsModel = VcpParams

    def run(self, inputs: list[pd.DataFrame], params: VcpParams, context: ExecutionContext) -> pd.DataFrame:
        from backend.alphaforge_policy import normalize_vcp_score
        # ... (rest of the preamble remains similar)
        df = inputs[0]
        if df.empty or not context.krx_client:
            return df

        lookback = params.lookback_days
        pivot_w = algo_settings.vcp_pivot_window
        max_depth = algo_settings.vcp_max_depth_pct
        vcp_cfg = config_section("vcp")
        start_date = prev_trading_day(context.as_of_date, n=lookback + 60)
        codes = df["code"].tolist()[:200]
        p_count = 6 if context.is_single_analysis else 3
        ohlcv_dict = context.krx_client.get_ohlcv_batch(codes, start_date, context.as_of_date, pages=p_count)

        results = []
        min_len = 60 if not context.is_single_analysis else 20

        for _, row in df.iterrows():
            row_dict = row.to_dict()
            code = row["code"]
            hist = ohlcv_dict.get(code)
            
            rs_val = float(row.get("rs_percentile", row.get("rs_rating", 0)) or 0)
            ma_flag = str(row.get("ma_alignment_flag", ""))

            if hist is None or hist.empty or len(hist) < min_len:
                row_dict.update({
                    "vcp_raw_score": None,
                    "vcp_effective_score": None,
                    "vcp_display_score": None,
                    "vcp_score": None,
                    "change_pct": 0.0,
                    "vcp_status": "DATA_MISSING",
                    "vcp_flag": "DATA_MISSING",
                    "adjusted_box_limit": pd.NA,
                    "stock_atr_multiplier": pd.NA,
                    "vcp_data_rows": len(hist) if hist is not None else 0,
                    "vcp_confidence": "DATA_INSUFFICIENT",
                    "vcp_warning": f"VCP 가격 데이터 부족 (OHLCV {len(hist) if hist is not None else 0}개 < {min_len}개)",
                    "vcp_width_score": 0,
                    "vcp_atr_score": 0,
                    "vcp_penalty_reasons": ["DATA_MISSING"],
                    "vcp_bonus_reasons": []
                })
                results.append(row_dict)
                continue

            # ... (OHLCV analysis remains similar)
            assert_no_future_data(hist, context.as_of_date, context=f"VcpNode:{code}")
            vcp_hist = hist.iloc[-lookback:] if len(hist) > lookback else hist
            highs = vcp_hist["high"].values
            lows = vcp_hist["low"].values
            n = len(vcp_hist)

            today_close = float(vcp_hist["close"].iloc[-1])
            prev_close = float(vcp_hist["close"].iloc[-2]) if len(vcp_hist) >= 2 else today_close
            change_pct = (today_close / prev_close - 1.0) if prev_close > 0 else 0.0
            max_high = float(vcp_hist["high"].max())
            dist_from_high = (max_high - today_close) / max_high if max_high > 0 else 1.0
            ma200 = hist["close"].rolling(200).mean().iloc[-1] if len(hist) >= 200 else hist["close"].mean()
            
            # ATR 및 변동성 계산
            atr_window = vcp_hist.tail(14)
            atr_val = (atr_window["high"] - atr_window["low"]).mean()
            atr_pct = (atr_val / today_close) * 100 if today_close > 0 and len(atr_window) >= 5 else 5.0
            
            recent_vol_window = vcp_hist.tail(10)
            recent_volatility = (
                (recent_vol_window["high"].max() - recent_vol_window["low"].min()) / today_close
                if today_close > 0 and len(recent_vol_window) >= 5 else 1.0
            )

            stock_atr_multiplier = max(
                float(vcp_cfg.get("stock_atr_multiplier_min", 0.85)),
                min(float(vcp_cfg.get("stock_atr_multiplier_max", 1.25)), atr_pct / 5.0),
            )
            macro_vix = row.get("macro_vix")
            macro_vix = float(macro_vix) if macro_vix is not None and not pd.isna(macro_vix) else None
            mv_cfg = vcp_cfg.get("market_volatility_multiplier", {})
            if macro_vix is not None and macro_vix >= 35:
                market_volatility_multiplier = float(mv_cfg.get("crisis", 1.0))
            elif macro_vix is not None and macro_vix >= 25:
                market_volatility_multiplier = float(mv_cfg.get("elevated", 1.15))
            else:
                market_volatility_multiplier = float(mv_cfg.get("normal", 1.0))
            adjusted_box_limit = float(vcp_cfg.get("base_box_limit", max_depth)) * market_volatility_multiplier * stock_atr_multiplier
            
            # --- VCP Component Scoring (Added for Diagnostics) ---
            vcp_penalty_reasons = []
            vcp_bonus_reasons = []
            
            # 1. Width Score (수축폭 점수)
            vcp_width_score = 0
            if n >= pivot_w * 2 + 1:
                pivot_indices = []
                for i in range(pivot_w, n - pivot_w):
                    window_highs = highs[i - pivot_w:i + pivot_w + 1]
                    if highs[i] == max(window_highs) and all(highs[j] != highs[i] for j in range(i - pivot_w, i)):
                        pivot_indices.append(i)

                contractions: list[float] = []
                if len(pivot_indices) >= 2:
                    for k in range(len(pivot_indices) - 1):
                        prev_high = highs[pivot_indices[k]]
                        period_low = min(lows[pivot_indices[k]:pivot_indices[k + 1] + 1])
                        contractions.append((prev_high - period_low) / prev_high * 100 if prev_high > 0 else 0.0)
                    last_high = highs[pivot_indices[-1]]
                    final_low = min(lows[pivot_indices[-1]:])
                    contractions.append((last_high - final_low) / last_high * 100 if last_high > 0 else 0.0)

                valid_contractions = [d for d in contractions if d < 25.0]
                max_contraction = max(contractions) if contractions else 0.0
                
                if max_contraction > 0:
                    if max_contraction <= adjusted_box_limit:
                        vcp_width_score = 40
                        vcp_bonus_reasons.append("WIDTH_WITHIN_LIMIT")
                    elif max_contraction <= adjusted_box_limit * 1.5:
                        vcp_width_score = 20
                        vcp_penalty_reasons.append("WIDTH_SLIGHTLY_DEEP")
                    else:
                        vcp_width_score = 5
                        vcp_penalty_reasons.append("WIDTH_TOO_DEEP")
            else:
                pivot_indices = []
                valid_contractions = []
                max_contraction = 0.0
                vcp_width_score = 0
                vcp_penalty_reasons.append("DATA_TOO_SHORT")

            # 2. ATR / Tightness Score (가격 긴장도 점수)
            vcp_atr_score = 0
            if atr_pct <= 3.0:
                vcp_atr_score = 30
                vcp_bonus_reasons.append("LOW_ATR_TIGHTNESS")
            elif atr_pct <= 5.0:
                vcp_atr_score = 20
            elif atr_pct > 8.0:
                vcp_penalty_reasons.append("HIGH_ATR_VOLATILITY")

            # ... (Existing logic for status and flags)
            is_high_consolidation = dist_from_high < 0.12 and today_close > ma200 and recent_volatility < 0.15
            is_near_setup = dist_from_high < 0.18 and recent_volatility < 0.22
            is_strong_leader = rs_val >= 90 and dist_from_high < 0.2

            reverse_pairs = sum(
                1 for i in range(1, len(valid_contractions))
                if valid_contractions[i] > valid_contractions[i - 1]
            )
            is_reverse = len(valid_contractions) >= 3 and reverse_pairs / max(len(valid_contractions) - 1, 1) > 0.5
            
            width_trend = "STABLE"
            if len(valid_contractions) >= 2:
                if valid_contractions[-1] < valid_contractions[-2] * 0.8: width_trend = "CONTRACTING"
                elif valid_contractions[-1] > valid_contractions[-2] * 1.2: width_trend = "EXPANDING"

            warnings = []
            vcp_reason_codes = []
            volume_declining = False
            volume_expanding = False
            dryup_score = 0
            
            if is_reverse:
                warnings.append("역수축 경고")
                vcp_reason_codes.append("REVERSE_EXPANSION")
                vcp_penalty_reasons.append("REVERSE_EXPANSION_FOUND")
            if max_contraction > adjusted_box_limit:
                warnings.append(f"수축폭 과대 {max_contraction:.1f}%")
                vcp_reason_codes.append("WIDTH_TOO_DEEP")
            
            if len(hist) >= 21:
                avg_volume = float(hist["volume"].iloc[-21:-1].mean() or 0)
                today_volume = float(hist["volume"].iloc[-1] or 0)
                prior_volume = float(hist["volume"].iloc[-41:-21].mean() or 0) if len(hist) >= 41 else avg_volume
                
                volume_declining = prior_volume > 0 and avg_volume <= prior_volume * 0.85
                volume_expanding = prior_volume > 0 and avg_volume > prior_volume * 1.25
                
                if volume_declining:
                    dryup_score = 70 if avg_volume <= prior_volume * 0.6 else 40
                    vcp_bonus_reasons.append("VOLUME_DRYUP")
                
                if not volume_declining:
                    warnings.append("거래량 미감소")
                    vcp_reason_codes.append("VOLUME_NOT_DRYING")
                if volume_expanding:
                    warnings.append("거래량 확대")
                    vcp_reason_codes.append("VOLUME_EXPANDING")
                    vcp_penalty_reasons.append("VOLUME_EXPANDING")

            severe_warning = is_reverse or max_contraction > adjusted_box_limit or volume_expanding
            warning_count = len(warnings)
            
            strict_ready = (
                len(valid_contractions) >= 3
                and max_contraction <= adjusted_box_limit
                and not severe_warning
                and volume_declining
                and dist_from_high <= 0.12
                and recent_volatility <= 0.18
            )
            valid_ready = (
                len(valid_contractions) >= 2
                and max_contraction <= adjusted_box_limit * 1.15
                and not is_reverse
                and dist_from_high <= 0.18
                and recent_volatility <= 0.24
            )

            ma20 = vcp_hist["close"].rolling(20).mean().iloc[-1] if len(vcp_hist) >= 20 else today_close
            disparity_20 = (today_close / ma20) if ma20 > 0 else 1.0
            recent_5d_gain = (today_close / vcp_hist["close"].iloc[-6] - 1.0) if len(vcp_hist) >= 6 else 0.0
            is_overextended = disparity_20 > 1.25 or recent_5d_gain > 0.40

            if is_reverse:
                status = "REVERSE_EXPANSION"
                score = 35
            elif is_overextended:
                status = "RALLY_EXHAUSTION"
                score = 40
                warnings.append(f"랠리 피로도 (20일 이격 {disparity_20:.2f})")
                vcp_reason_codes.append("RALLY_EXHAUSTION")
                vcp_penalty_reasons.append("RALLY_EXHAUSTION")
            elif strict_ready and warning_count == 0:
                status = "VCP_STRICT"
                score = 92 if len(valid_contractions) == 3 else 98
                vcp_bonus_reasons.append("STRICT_SETUP_BONUS")
            elif valid_ready and warning_count <= 1:
                status = "VCP_VALID"
                score = 78
                vcp_bonus_reasons.append("VALID_SETUP_BONUS")
            elif severe_warning or warning_count >= 2:
                status = "VCP_WARNING"
                score = 45
            elif is_high_consolidation:
                status = "HIGH_CONSOLIDATION"
                score = 75
                vcp_bonus_reasons.append("HIGH_CONSOLIDATION_BONUS")
            elif is_strong_leader:
                status = "STRONG_LEADER_NO_PIVOT"
                score = 65
            elif is_near_setup:
                status = "NEAR_SETUP"
                score = 60
            elif len(valid_contractions) >= 1:
                status = "BASE_BUILDING"
                score = 50
            else:
                status = "NOT_READY"
                score = 35

            raw, eff, disp, conf, cross = normalize_vcp_score({
                "vcp_score": score,
                "rs_percentile": rs_val,
                "ma_alignment_flag": ma_flag,
                "primary_bucket": "PENDING"
            })
            
            row_dict.update({
                "vcp_raw_score": int(raw),
                "vcp_effective_score": int(eff),
                "vcp_display_score": int(disp),
                "vcp_score": int(disp),
                "change_pct": round(change_pct, 4),
                "vcp_status": status,
                "vcp_flag": status,
                "vcp_data_rows": n,
                "vcp_width_trend": width_trend,
                "vcp_contraction_count": len(valid_contractions),
                "vcp_last_base_width_pct": round(valid_contractions[-1], 2) if valid_contractions else 0.0,
                "vcp_prev_base_width_pct": round(valid_contractions[-2], 2) if len(valid_contractions) >= 2 else 0.0,
                "vcp_atr_trend": round(atr_pct, 2),
                "vcp_volume_dryup_score": dryup_score,
                "vcp_volume_trend": "DECLINING" if volume_declining else ("EXPANDING" if volume_expanding else "STABLE"),
                "vcp_price_tightness_score": int((1.0 - recent_volatility) * 100),
                "vcp_reverse_expansion_flag": is_reverse,
                "vcp_rally_exhaustion_flag": is_overextended,
                "vcp_reason_codes": vcp_reason_codes,
                "vcp_confidence": conf,
                "vcp_cross_warning": cross,
                "vcp_width_score": vcp_width_score,
                "vcp_atr_score": vcp_atr_score,
                "vcp_penalty_reasons": vcp_penalty_reasons,
                "vcp_bonus_reasons": vcp_bonus_reasons,
                "adjusted_box_limit": round(adjusted_box_limit, 2),
                "stock_atr_multiplier": round(stock_atr_multiplier, 3),
                "vcp_warning": " | ".join(warnings) if warnings else status,
            })
            results.append(row_dict)

        return pd.DataFrame(results)
