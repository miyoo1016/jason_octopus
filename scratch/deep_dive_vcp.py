import pandas as pd
from data.naver_krx import NaverKRXClient
from backend.config import settings
from datetime import datetime

def analyze_vcp_raw(code, name):
    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    today = "2026-05-08"
    ohlcv = krx.get_ohlcv(code, pages=2) # ~120 days
    
    print(f"=== {name} ({code}) 수치 분석 ===")
    print(ohlcv.tail(10)[["open", "high", "low", "close", "volume"]])
    
    # Calculate daily volatility (High-Low % of High)
    ohlcv["vol"] = (ohlcv["high"] - ohlcv["low"]) / ohlcv["high"] * 100
    print("\n[최근 변동성(H-L %) 추이 - 작을수록 수축]")
    print(ohlcv["vol"].tail(5))
    
    # Compare with 20-day avg volatility
    avg_vol = ohlcv["vol"].iloc[-21:-1].mean()
    print(f"\n최근 5일 평균 변동성: {ohlcv['vol'].tail(5).mean():.2f}%")
    print(f"이전 20일 평균 변동성: {avg_vol:.2f}%")
    
    if ohlcv['vol'].tail(5).mean() < avg_vol:
        print("\n✅ 판정: 변동성이 평균 대비 수축 중 (VCP 징후)")
    else:
        print("\n❌ 판정: 변동성 확장 중")

if __name__ == "__main__":
    analyze_vcp_raw("007070", "GS리테일")
    analyze_vcp_raw("061090", "세나테크놀로지")
