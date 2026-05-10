import pandas as pd
from engine.dag import DAG
from engine.cache import ResultCache
from data.naver_krx import NaverKRXClient
from backend.config import settings
import nodes
from datetime import datetime

async def diagnostic_screening():
    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    cache = ResultCache(cache_dir=settings.data_cache_dir)
    today = "2026-05-08" # As in user's state
    
    # Mirror user's strict settings
    dag = DAG(name="diagnostic")
    dag.add_node("n1", nodes.UniverseNode(), {"market": "ALL"})
    dag.add_node("n2", nodes.LiquidityFilterNode(), {"min_trading_value_krw": 2000000000})
    dag.add_node("n3", nodes.VcpNode(), {"lookback_days": 120, "min_score": 70})
    dag.add_node("n4", nodes.BoxBreakoutNode(), {"box_period": 60, "breakout_pct": 1.0, "vol_C": 1.5})
    dag.add_node("n5", nodes.MaAlignmentNode(), {})
    dag.add_node("n6", nodes.RsRatingNode(), {"min_rating": 80})
    dag.add_node("n7", nodes.ScoreFilterNode(), {})
    
    dag.add_edge("n1", "n2")
    dag.add_edge("n2", "n3")
    dag.add_edge("n3", "n4")
    dag.add_edge("n4", "n5")
    dag.add_edge("n5", "n6")
    dag.add_edge("n6", "n7")
    
    print(f"--- Diagnostic Run for {today} ---")
    result = dag.execute(today, cache, krx_client=krx)
    
    for log in result.node_logs:
        print(f"Node {log.node_id} ({log.display_name}): In={log.input_count}, Out={log.output_count}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(diagnostic_screening())
