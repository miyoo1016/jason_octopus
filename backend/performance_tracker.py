import json
import os
import pandas as pd
from typing import Dict, Any, List

SNAPSHOTS_FILE = "data/history/alphaforge_performance_snapshots.jsonl"

def _safe_float(val: Any, default: Any = 0.0) -> Any:
    try:
        if pd.isna(val) or val is None:
            return default
        return float(val)
    except (ValueError, TypeError):
        return default

def _calculate_mdd_close(prices: List[float]) -> float:
    if not prices or len(prices) < 2:
        return 0.0
    mdd = 0.0
    peak = prices[0]
    for p in prices:
        if p > peak:
            peak = p
        if peak > 0:
            dd = (p - peak) / peak * 100
            if dd < mdd:
                mdd = dd
    return mdd

def save_snapshots(df: pd.DataFrame, as_of_date: str) -> None:
    if df is None or df.empty:
        return
    
    os.makedirs(os.path.dirname(SNAPSHOTS_FILE), exist_ok=True)
    
    records = []
    for _, row in df.iterrows():
        close_val = _safe_float(row.get("close", 0), None)
        if close_val is not None and close_val <= 0:
            close_val = None
            
        record = {
            "date": as_of_date,
            "symbol": str(row.get("code", row.get("symbol", ""))),
            "name": str(row.get("name", "")),
            "close_price": close_val,
            "final_label": str(row.get("final_label", row.get("display_label", ""))),
            "display_label": str(row.get("display_label", "")),
            "legacy_label": str(row.get("watch_alert_type", "")),
            "tier": str(row.get("primary_bucket", "")),
            "total_score": _safe_float(row.get("total_score", 0)),
            "rs_score": _safe_float(row.get("rs_percentile", 0)),
            "vcp_raw_score": _safe_float(row.get("vcp_raw_score", 0)),
            "vcp_effective_score": _safe_float(row.get("vcp_effective_score", 0)),
            "vcp_display_score": _safe_float(row.get("vcp_display_score", 0)),
            "vcp_status": str(row.get("vcp_status", "")),
            "vcp_component_scores": row.get("vcp_component_scores") if pd.notna(row.get("vcp_component_scores")) else None,
            "buy_gate_passed": bool(row.get("buy_gate_passed", False)),
            "failed_buy_gates": row.get("failed_buy_gates", []) if isinstance(row.get("failed_buy_gates"), list) else [],
            "market_regime": str(row.get("dominant_regime", "")),
            "sector": str(row.get("sector", "")),
            "breakout_status": str(row.get("breakout_status", "")),
            "volume_ratio": _safe_float(row.get("breakout_volume_ratio", 0), None),
            "trading_value": _safe_float(row.get("liquidity_trading_value", 0), None)
        }
        records.append(record)
        
    with open(SNAPSHOTS_FILE, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def get_performance_summary(current_df: pd.DataFrame, current_date: str) -> Dict[str, Any]:
    if not os.path.exists(SNAPSHOTS_FILE):
        return {"status": "DATA_INSUFFICIENT", "by_label": {}, "message": "성과 추적 데이터 부족"}
        
    snapshots = []
    with open(SNAPSHOTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    snapshots.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                    
    if not snapshots:
        return {"status": "DATA_INSUFFICIENT", "by_label": {}, "message": "성과 추적 데이터 부족"}
        
    hist_df = pd.DataFrame(snapshots)
    if hist_df.empty or "date" not in hist_df.columns:
        return {"status": "DATA_INSUFFICIENT", "by_label": {}, "message": "성과 추적 데이터 부족"}
        
    # Deduplicate: date + symbol keeping the last one
    hist_df = hist_df.drop_duplicates(subset=["date", "symbol"], keep="last")
    
    unique_dates = sorted(hist_df["date"].unique().tolist())
    if current_date not in unique_dates:
        unique_dates.append(current_date)
        unique_dates.sort()
        
    current_idx = unique_dates.index(current_date)
    
    # Get current prices from current_df
    if current_df.empty or "code" not in current_df.columns:
        return {"status": "DATA_INSUFFICIENT", "by_label": {}, "message": "현재 가격 데이터 없음"}
        
    current_prices = {}
    for _, row in current_df.iterrows():
        code = str(row.get("code", ""))
        val = _safe_float(row.get("close", 0), None)
        if val is not None and val > 0:
            current_prices[code] = val
            
    target_labels = ["BUY_CANDIDATE", "NEAR_BUY", "PRIORITY_WATCH", "RISK_WATCH", "SETUP_WATCH", "REJECTED"]
    by_label = {
        lbl: {
            "return_5d": None,
            "return_10d": None,
            "return_20d": None,
            "max_drawdown_10d_close": None
        } for lbl in target_labels
    }
    
    has_any_data = False
    
    # 1. Calculate Returns
    for offset, key in [(5, "return_5d"), (10, "return_10d"), (20, "return_20d")]:
        target_idx = current_idx - offset
        if target_idx >= 0:
            target_date = unique_dates[target_idx]
            past_snaps = hist_df[hist_df["date"] == target_date]
            
            for label in target_labels:
                label_snaps = past_snaps[past_snaps["final_label"] == label]
                if not label_snaps.empty:
                    returns = []
                    for _, row in label_snaps.iterrows():
                        sym = row["symbol"]
                        past_price = row.get("close_price")
                        if past_price is not None:
                            past_price = _safe_float(past_price, None)
                        if sym in current_prices and past_price is not None and past_price > 0:
                            curr_price = current_prices[sym]
                            returns.append((curr_price - past_price) / past_price * 100)
                    
                    if returns:
                        by_label[label][key] = sum(returns) / len(returns)
                        has_any_data = True
                        
    # 2. Calculate max_drawdown_10d_close
    target_idx_10 = current_idx - 10
    if target_idx_10 >= 0:
        target_date_10 = unique_dates[target_idx_10]
        period_dates = unique_dates[target_idx_10:current_idx + 1]
        period_df = hist_df[hist_df["date"].isin(period_dates[:-1])].copy() # exclude today from df
        
        # Sort to ensure chronological order
        period_df = period_df.sort_values("date")
        
        past_snaps_10 = hist_df[hist_df["date"] == target_date_10]
        for label in target_labels:
            label_symbols = past_snaps_10[past_snaps_10["final_label"] == label]["symbol"].tolist()
            if label_symbols:
                mdds = []
                for sym in label_symbols:
                    sym_df = period_df[period_df["symbol"] == sym]
                    prices = []
                    for d in period_dates:
                        if d == current_date:
                            if sym in current_prices:
                                prices.append(current_prices[sym])
                        else:
                            match = sym_df[sym_df["date"] == d]
                            if not match.empty:
                                p = _safe_float(match.iloc[0].get("close_price"), None)
                                if p is not None and p > 0:
                                    prices.append(p)
                    
                    if len(prices) >= 2:
                        mdds.append(_calculate_mdd_close(prices))
                
                if mdds:
                    by_label[label]["max_drawdown_10d_close"] = sum(mdds) / len(mdds)
                    has_any_data = True

    if not has_any_data:
        return {"status": "DATA_INSUFFICIENT", "by_label": by_label, "message": "성과 추적 데이터 부족"}
        
    return {
        "status": "READY",
        "by_label": by_label,
        "message": "성과 추적 요약 생성됨"
    }
