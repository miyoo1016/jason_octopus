import sys
import os
sys.path.append(os.getcwd())
from data.naver_krx import NaverKRXClient
import time
from data.holidays import prev_trading_day

client = NaverKRXClient()
df_univ = client.get_universe("2026-05-04", market="ALL")
codes = df_univ["code"].tolist()
print(f"Total codes: {len(codes)}")

start = time.time()
ohlcv_dict = client.get_ohlcv_batch(codes, "2025-05-04", "2026-05-04")
print(f"Time taken: {time.time() - start:.2f}s")
