import yfinance as yf
import pandas as pd
import json
from datetime import datetime
import os
import math

US_UNIVERSE = [
    "QQQ", "SPY", "SOXX", "SMH", "NVDA", "AMD", "AVGO", "ASML", "TSM", 
    "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "PLTR", "CRM", "NOW", 
    "NFLX", "JPM", "LLY"
]

def safe_float(val, default=0.0):
    try:
        if pd.isna(val) or math.isnan(val):
            return default
        return float(val)
    except:
        return default

def calculate_indicators(df):
    if df is None or df.empty or len(df) < 50:
        return None
    
    close = df['Close'].astype(float)
    volume = df['Volume'].astype(float)
    
    current_price = safe_float(close.iloc[-1])
    prev_price = safe_float(close.iloc[-2])
    
    change_pct = ((current_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0
    
    ma20 = close.rolling(window=20).mean().iloc[-1]
    ma50 = close.rolling(window=50).mean().iloc[-1]
    ma200 = close.rolling(window=200).mean().iloc[-1] if len(close) >= 200 else close.mean()
    
    # Simple RS (relative to itself 6mo ago)
    six_mo_ago_price = safe_float(close.iloc[0])
    rs_raw = ((current_price - six_mo_ago_price) / six_mo_ago_price) * 100 if six_mo_ago_price > 0 else 0
    
    box_upper = close.rolling(window=20).max().iloc[-1]
    
    return {
        "current_price": current_price,
        "change_pct": change_pct,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "rs_raw": rs_raw,
        "box_upper": box_upper
    }

def generate_candidates():
    print(f"Downloading data for US universe ({len(US_UNIVERSE)} symbols)...")
    try:
        data = yf.download(US_UNIVERSE, period="6mo", group_by="ticker", auto_adjust=True, progress=False)
    except Exception as e:
        print(f"Error downloading data: {e}")
        return []

    results = []
    for symbol in US_UNIVERSE:
        if symbol in data:
            df = data[symbol].dropna()
        else:
            # If group_by didn't create a multi-level dict (e.g. single ticker or some other struct)
            # fallback for single ticker if necessary, but we have >1 so it should be multi
            df = pd.DataFrame()

        if df.empty:
            continue
            
        indicators = calculate_indicators(df)
        if not indicators:
            continue
            
        current_price = indicators["current_price"]
        ma20 = indicators["ma20"]
        ma50 = indicators["ma50"]
        ma200 = indicators["ma200"]
        change_pct = indicators["change_pct"]
        rs_raw = indicators["rs_raw"]
        box_upper = indicators["box_upper"]
        
        # Scoring logic v1
        short_score = 50
        mid_score = 50
        
        # Trend
        if current_price > ma20: short_score += 15
        if current_price > ma50: mid_score += 15
        if ma20 > ma50 > ma200: 
            short_score += 20
            mid_score += 20
            
        # Momentum
        if change_pct > 2: short_score += 15
        if rs_raw > 20: mid_score += 15
        
        # Cap at 100
        short_score = min(100, short_score)
        mid_score = min(100, mid_score)
        
        total_score = short_score + mid_score
        
        alert = "SETUP_WATCH" if short_score > 70 else "WATCH"
        if short_score > 85: alert = "ACTION_ALERT"
        
        vcp = "VCP_LIKE" if 0 < (box_upper - current_price) / current_price < 0.05 else "NO_VCP"
        horizon = "POSITION_SWING" if mid_score > 80 else "SHORT_SWING"
        
        reasons = []
        if current_price > ma20 > ma50: reasons.append("정배열")
        if change_pct > 2: reasons.append(f"급등 {change_pct:.1f}%")
        if vcp == "VCP_LIKE": reasons.append("박스 상단 근접")
        reason = ", ".join(reasons) if reasons else "특이사항 없음"
        
        # Fake RS mapping (0-99 percentile)
        rs_mapped = min(99, max(10, int(rs_raw * 1.5)))
        
        results.append({
            "symbol": symbol,
            "name": symbol, # fallback
            "sector": "US_TECH" if symbol in ["QQQ", "NVDA", "AMD", "MSFT", "AAPL", "GOOGL", "AMZN", "META"] else "US_STOCK",
            "alert": alert,
            "rs": rs_mapped,
            "vcp": vcp,
            "box_upper": float(box_upper),
            "short_score": int(short_score),
            "mid_score": int(mid_score),
            "total_score": total_score,
            "horizon": horizon,
            "reason": reason,
            "data_quality": "HIGH"
        })
        
    # Sort and pick top 10
    results.sort(key=lambda x: x["total_score"], reverse=True)
    top_candidates = results[:10]
    
    # Remove temporary sort key
    for c in top_candidates:
        del c["total_score"]
        
    return top_candidates

def main():
    print("Running US AlphaForge v1...")
    candidates = generate_candidates()
    
    export_data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "market": "US",
        "universe_count": len(US_UNIVERSE),
        "candidates": candidates
    }
    
    os.makedirs("data/exports", exist_ok=True)
    export_path = "data/exports/alphaforge_us_candidates.json"
    
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
        
    print(f"Exported {len(candidates)} candidates to {export_path}")

if __name__ == "__main__":
    main()
