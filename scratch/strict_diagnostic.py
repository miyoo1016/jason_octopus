import pandas as pd
from engine.dag import DAG
from engine.cache import ResultCache
from data.naver_krx import NaverKRXClient
from backend.config import settings
import nodes
from datetime import datetime

async def detailed_diagnostic():
    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    cache = ResultCache(cache_dir=settings.data_cache_dir)
    today = "2026-05-08"
    
    dag = DAG(name="strict_test")
    dag.add_node("n1", nodes.UniverseNode(), {"market": "ALL"})
    dag.add_node("n2", nodes.LiquidityFilterNode(), {"min_trading_value_krw": 2000000000}) # 20억
    dag.add_node("n3", nodes.VcpNode(), {"lookback_days": 120, "min_score": 70}) # VCP 70
    dag.add_node("n4", nodes.BoxBreakoutNode(), {"box_period": 60, "breakout_pct": 1.0, "vol_C": 1.5}) # 1%, 1.5x
    dag.add_node("n5", nodes.MaAlignmentNode(), {}) # 정배열
    dag.add_node("n6", nodes.RsRatingNode(), {"min_rating": 80}) # RS 80
    
    dag.add_edge("n1", "n2")
    dag.add_edge("n2", "n3")
    dag.add_edge("n3", "n4")
    dag.add_edge("n4", "n5")
    dag.add_edge("n5", "n6")
    
    print(f"--- Strict AND Diagnostic (2026-05-08) ---")
    result = dag.execute(today, cache, krx_client=krx)
    
    for log in result.node_logs:
        print(f"Node {log.node_id} ({log.display_name}): Out={log.output_count}")
        if log.output_count > 0 and log.output_count < 10:
            df = result.outputs[log.node_id]
            print(f"  Surviving stocks: {df['name'].tolist()}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(detailed_diagnostic())
