import asyncio
import pandas as pd
from pathlib import Path
from datetime import datetime
from backend.algo_settings import algo_settings as settings
from data.naver_krx import NaverKRXClient
from engine.dag import DAG
from engine.cache import ResultCache
from data.holidays import prev_trading_day
import nodes
from backend.analysis_summary import build_analysis_payload

async def validate_n(n: int):
    print(f"\n{'='*20} Validation Report (max_symbols={n}) {'='*20}")
    cache_dir = Path("data/cache")
    krx = NaverKRXClient(cache_dir=cache_dir)
    cache = ResultCache(cache_dir=cache_dir)
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
            "node_id": log.node_id, "node_type": log.node_type,
            "input_count": log.input_count, "output_count": len(df),
            "status": log.status, "latency_ms": log.latency_ms, "cache_hit": log.cache_hit
        }
        print(f"  {log.node_id:12s} ({log.node_type:15s}): {log.input_count:4d} -> {len(df):4d}")
        
    payload = build_analysis_payload(result, node_results)
    s = payload['summary']
    print(f"\n[Summary] Universe: {s['universe_count']}, Primary Total: {s['primary_count_total']}")
    print(f"  Buckets: {s['primary_counts']}")
    print(f"  Watch Alert: True({s['watchlist_flag_true_count']}), False({s['watchlist_flag_false_count']})")
    
    if payload['diagnostics']['data_quality_warnings']:
        print(f"  Warnings: {payload['diagnostics']['data_quality_warnings']}")
        
    print("\n[Top Promotion Reasons]")
    for item in payload['diagnostics'].get('top_promotion_reasons', []):
        print(f"  {item['reason']}: {item['count']}")
        
    print("\n[Top Tier Downgrade Reasons]")
    for item in payload['diagnostics'].get('top_downgrade_reasons', []):
        print(f"  {item['reason']}: {item['count']}")
        
    print("\n[Top Rejected Reasons]")
    for item in payload['diagnostics'].get('top_rejection_reasons', []):
        print(f"  {item['reason']}: {item['count']}")

    print("\n[Top Risk Watch Retention Reasons]")
    for item in payload['diagnostics'].get('top_risk_watch_reasons', []):
        print(f"  {item['reason']}: {item['count']}")

    final_df = result.outputs.get("top", pd.DataFrame())
    dual_cols = [
        "name", "primary_bucket", "short_swing_score", "position_swing_score",
        "horizon_label", "short_reasons", "position_reasons",
    ]
    dual_cols = [c for c in dual_cols if c in final_df.columns]
    if dual_cols:
        print("\n[Dual Horizon]")
        print(final_df[dual_cols].head(10).to_string(index=False))

async def main():
    for n in [30, 100]:
        await validate_n(n)

if __name__ == "__main__":
    asyncio.run(main())
