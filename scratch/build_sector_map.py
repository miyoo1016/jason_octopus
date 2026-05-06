import requests
import re
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
import logging
import os

logger = logging.getLogger(__name__)

def build_perfect_sector_map():
    url = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = "euc-kr"
        html = res.text
        # 업종 링크 추출: <a href="/sise/sise_group_detail.naver?type=upjong&no=278">반도체와반도체장비</a>
        matches = re.findall(r'href="(/sise/sise_group_detail\.naver\?type=upjong&no=(\d+))">(.*?)</a>', html)
    except Exception as e:
        print(f"❌ 업종 리스트 수집 실패: {e}")
        return {}

    sector_map = {}
    
    def fetch_stocks_in_sector(m):
        path, no, name = m
        try:
            detail_url = f"https://finance.naver.com{path}"
            r = requests.get(detail_url, headers=headers, timeout=10)
            r.encoding = "euc-kr"
            codes = re.findall(r'code=(\d{6})', r.text)
            return name, set(codes)
        except:
            return name, set()

    print(f"🚀 {len(matches)}개 업종 분석 시작 (병렬)...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(fetch_stocks_in_sector, matches)
        for name, codes in results:
            for c in codes:
                sector_map[c] = name

    if sector_map:
        os.makedirs("data", exist_ok=True)
        pd.DataFrame([{"code": c, "sector": s} for c, s in sector_map.items()]).to_csv("data/krx_sector.csv", index=False)
        print(f"✅ 섹터 지도 제작 완료: {len(sector_map)}종목 매핑됨")
        print(f"   - 삼성전자(005930): {sector_map.get('005930')}")
        print(f"   - 미래에셋증권(006800): {sector_map.get('006800')}")
    
    return sector_map

if __name__ == "__main__":
    build_perfect_sector_map()
