import pandas as pd
from data.naver_krx import NaverKRXClient
from backend.config import settings
from nodes.rs_rating import RsRatingNode, RsRatingParams
from engine.node_base import ExecutionContext

async def check_rs():
    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    today = "2026-05-08"
    
    df = pd.DataFrame([
        {"code": "007070", "name": "GS리테일"},
        {"code": "061090", "name": "세나테크놀로지"}
    ])
    
    node = RsRatingNode()
    ctx = ExecutionContext(run_id="test", as_of_date=today, krx_client=krx)
    result = node.run([df], RsRatingParams(lookback_days=252, min_rating=0), ctx)
    
    print(result[["name", "rs_rating", "rs_score"]])

if __name__ == "__main__":
    import asyncio
    asyncio.run(check_rs())
