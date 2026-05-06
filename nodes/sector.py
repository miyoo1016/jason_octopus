"""
섹터 분류 매핑 노드 (3단계 Fallback 로직).
"""
import os
import time
import logging
import pandas as pd
import requests
from pydantic import BaseModel
from engine.node_base import BaseNode, ExecutionContext

logger = logging.getLogger(__name__)

_WICS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://m.stock.naver.com/",
}

def _build_sector_map_from_naver() -> dict[str, str]:
    """Naver WICS API로 전종목 섹터 맵 생성 후 data/krx_sector.csv에 저장."""
    base = "https://m.stock.naver.com/api/stocks/industry"
    try:
        res = requests.get(f"{base}?industryGroupCode=WICS&page=1&pageSize=200",
                           headers=_WICS_HEADERS, timeout=10)
        res.raise_for_status()
        groups = res.json().get("groups", [])
    except Exception as e:
        logger.warning("WICS 업종 그룹 수집 실패: %s", e)
        return {}

    code_to_sector: dict[str, str] = {}
    for g in groups:
        industry_no = g.get("no") or g.get("industryGroupCode")
        industry_name = g.get("name") or g.get("industryGroupName", "기타")
        page = 1
        while True:
            try:
                r2 = requests.get(f"{base}/{industry_no}?page={page}&pageSize=100",
                                  headers=_WICS_HEADERS, timeout=8)
                r2.raise_for_status()
                stocks = r2.json().get("stocks", [])
            except Exception:
                break
            if not stocks:
                break
            for s in stocks:
                code = s.get("itemCode", "")
                if code:
                    code_to_sector[code] = industry_name
            if len(stocks) < 100:
                break
            page += 1
            time.sleep(0.05)

    if code_to_sector:
        try:
            csv_df = pd.DataFrame(
                [{"code": c, "sector": s} for c, s in code_to_sector.items()]
            )
            os.makedirs("data", exist_ok=True)
            csv_df.to_csv("data/krx_sector.csv", index=False)
            logger.info("data/krx_sector.csv 저장 완료 (%d종목)", len(code_to_sector))
        except Exception as e:
            logger.warning("krx_sector.csv 저장 실패: %s", e)

    return code_to_sector

class SectorParams(BaseModel):
    fallback_name: str = "기타"

class SectorNode(BaseNode):
    NODE_TYPE      = "sector"
    DISPLAY_NAME   = "섹터 분류"
    DESCRIPTION    = "종목별 섹터 정보를 매핑합니다 (3단계 Fallback)."
    INPUT_ARITY    = 1
    OUTPUT_COLUMNS = ("sector",)
    ParamsModel    = SectorParams

    def run(self, inputs: list[pd.DataFrame], params: SectorParams, context: ExecutionContext) -> pd.DataFrame:
        df = inputs[0]
        if df.empty:
            df["sector"] = pd.Series(dtype=object)
            return df
            
        codes = df["code"].tolist()
        sector_map = {}

        # 1. 로컬 CSV 파일 (data/krx_sector.csv) 확인
        csv_path = "data/krx_sector.csv"
        if os.path.exists(csv_path):
            try:
                sector_df = pd.read_csv(csv_path, dtype=str)
                sector_map = dict(zip(sector_df["code"].str.zfill(6), sector_df["sector"]))
                logger.info("data/krx_sector.csv 파일로 섹터 매핑 완료 (%d종목)", len(sector_map))
            except Exception as e:
                logger.warning("krx_sector.csv 읽기 실패: %s", e)

        # 2. ETF/ETN 사전 분류 (CSV에 없는 경우 대비)
        ETF_KEYWORDS = ["KODEX", "TIGER", "KBSTAR", "HANARO", "KOSEF", "ACE", "SOL",
                        "ARIRANG", "TIMEFOLIO", "KINDEX", "KTOP", "PLUS", "TREX", "FOCUS",
                        "RISE", "SMART", "WOORI", "MIRAE", "TAMA", "VITESSE"]
        
        sectors = []
        for code in codes:
            sec = sector_map.get(code)
            name = df.loc[df["code"] == code, "name"].values[0] if "name" in df.columns else ""
            name = str(name).upper()
            
            # ETF/ETN 테마 세분화 로직 (Perplexity 요청 사항)
            is_etf = name.endswith("ETF") or name.endswith("ETN") or any(kw in name for kw in ETF_KEYWORDS)
            
            if is_etf or sec == "기타" or not sec:
                if "반도체" in name:
                    sec = "반도체ETF"
                elif "레버리지" in name:
                    sec = "지수레버리지ETF"
                elif "인버스" in name:
                    sec = "인버스ETF"
                elif "2차전지" in name:
                    sec = "2차전지ETF"
                elif is_etf:
                    sec = "테마ETF"
                elif not sec:
                    sec = params.fallback_name
                    
            sectors.append(sec)
            
        result = df.copy()
        result["sector"] = sectors
        return result
