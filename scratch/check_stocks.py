import pandas as pd
from data.naver_krx import NaverKRXClient
from backend.config import settings
from datetime import datetime
from data.holidays import is_trading_day, prev_trading_day

def check_stock(code, name):
    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    as_of = today if is_trading_day(today) else prev_trading_day(today)
    
    # Get 120 days of data
    ohlcv = krx.get_ohlcv(code, pages=3) # ~180 days
    if ohlcv.empty:
        print(f"{name} ({code}): No data")
        return

    latest = ohlcv.iloc[-1]
    prev = ohlcv.iloc[-2]
    
    # 20-day avg volume
    avg_vol = ohlcv["volume"].iloc[-21:-1].mean()
    vol_ratio = latest["volume"] / avg_vol if avg_vol > 0 else 0
    
    # Box 60-day high
    box_high = ohlcv["close"].iloc[-61:-1].max()
    is_breakout = latest["close"] > box_high
    breakout_pct = (latest["close"] / box_high - 1) * 100 if box_high > 0 else 0
    
    print(f"--- {name} ({code}) ---")
    print(f"Price: {latest['close']:,} ({((latest['close']/prev['close']-1)*100):.2f}%)")
    print(f"Volume Ratio: {vol_ratio:.2f}x")
    print(f"60-day High: {box_high:,}")
    print(f"Is Breakout: {is_breakout} ({breakout_pct:.2f}%)")

if __name__ == "__main__":
    # Check Yesterday's winners
    check_stock("199800", "툴젠")
    check_stock("319660", "피에스케이")
    check_stock("178320", "서진시스템")
    check_stock("028050", "삼성E&A")
    # Check Today's candidates
    check_stock("007070", "GS리테일")
    check_stock("061090", "세나테크놀로지")
