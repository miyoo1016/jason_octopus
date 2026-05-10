import asyncio
import json
import pandas as pd
from datetime import datetime
from backend.config import settings
from data.naver_krx import NaverKRXClient
from data.holidays import prev_trading_day
from engine.cache import ResultCache
from engine.dag import DAG
import nodes
from backend.analysis_summary import build_analysis_payload

async def validate_with_n(n: int):
    print(f"\n{'='*20} Validation Report (max_symbols={n}) {'='*20}")
    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    cache = ResultCache(cache_dir=settings.data_cache_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    as_of_date = prev_trading_day(today, n=1)
    
    dag = DAG(name=f"validation_{n}")
    dag.add_node("universe", nodes.UniverseNode(), {"market": "ALL", "max_symbols": n})
    dag.add_node("liquidity", nodes.LiquidityFilterNode(), {})
    dag.add_node("vcp", nodes.VcpNode(), {})
    dag.add_node("box", nodes.BoxBreakoutNode(), {})
    dag.add_node("ma", nodes.MaAlignmentNode(), {})
    dag.add_node("foreign", nodes.ForeignFlowNode(), {})
    dag.add_node("institution", nodes.InstitutionFlowNode(), {})
    dag.add_node("rs", nodes.RsRatingNode(), {})
    dag.add_node("sector", nodes.SectorNode(), {})
    dag.add_node("macro", nodes.MacroFilterNode(), {})
    dag.add_node("score", nodes.ScoreFilterNode(), {})
    dag.add_node("top", nodes.TopNNode(), {"n": n})
    
    dag.add_edge("universe", "liquidity")
    dag.add_edge("liquidity", "vcp")
    dag.add_edge("vcp", "box")
    dag.add_edge("box", "ma")
    dag.add_edge("ma", "foreign")
    dag.add_edge("foreign", "institution")
    dag.add_edge("institution", "rs")
    dag.add_edge("rs", "sector")
    dag.add_edge("sector", "macro")
    dag.add_edge("macro", "score")
    dag.add_edge("score", "top")
    
    result = dag.execute(as_of_date, cache, krx_client=krx)
    if not result.success:
        print(f"FAILED: {result.error}")
        return
        
    node_results = {}
    for log in result.node_logs:
        df = result.outputs.get(log.node_id, pd.DataFrame())
        node_results[log.node_id] = {
            "node_id": log.node_id,
            "node_type": log.node_type,
            "input_count": log.input_count,
            "output_count": len(df),
            "status": log.status
        }
        
    payload = build_analysis_payload(result, node_results)
    
    s = payload['summary']
    print(f"Universe: {s['universe_count']}, Primary: {s['primary_counts']}")
    print(f"Watch Alert (True): {s['watchlist_flag_true_count']}, (False): {s['watchlist_flag_false_count']}")
    
    if payload['diagnostics']['data_quality_warnings']:
        print(f"Warnings: {payload['diagnostics']['data_quality_warnings']}")
        
    print("\n[Top Watch Reasons]")
    for item in payload['diagnostics'].get('top_watch_reasons', []):
        print(f"  {item['reason']}: {item['count']}")
        
    print("\n[Top Watch Exclusions]")
    for item in payload['diagnostics'].get('top_watch_exclusion_reasons', []):
        print(f"  {item['reason']}: {item['count']}")
        
    print("\n[Sample Results (Top 10)]")
    final_df = result.outputs["top"]
    cols = ["name", "primary_bucket", "watchlist_flag", "watch_reason", "watch_exclusion_reason"]
    print(final_df[cols].head(10).to_string(index=False))

async def main():
    await validate_with_n(30)
    await validate_with_n(100)

if __name__ == "__main__":
    asyncio.run(main())
