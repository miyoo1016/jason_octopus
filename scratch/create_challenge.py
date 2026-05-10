import pandas as pd
from engine.dag import DAG
from engine.cache import ResultCache
from data.naver_krx import NaverKRXClient
from backend.config import settings
import nodes
from datetime import datetime

async def generate_challenge():
    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    cache = ResultCache(cache_dir=settings.data_cache_dir)
    today = "2026-05-08"
    
    dag = DAG(name="challenge_wide")
    dag.add_node("n1", nodes.UniverseNode(), {"market": "ALL"})
    dag.add_node("n2", nodes.LiquidityFilterNode(), {"min_trading_value_krw": 1000000000})
    dag.add_node("n3", nodes.VcpNode(), {"lookback_days": 120, "min_score": 0})
    dag.add_node("n4", nodes.BoxBreakoutNode(), {"box_period": 60, "breakout_pct": -100})
    dag.add_node("n5", nodes.RsRatingNode(), {"min_rating": 0})
    dag.add_node("n6", nodes.ScoreFilterNode(), {})
    
    dag.add_edge("n1", "n2")
    dag.add_edge("n2", "n3")
    dag.add_edge("n3", "n4")
    dag.add_edge("n4", "n5")
    dag.add_edge("n5", "n6")
    
    # Use is_single=True to prevent strict filtering in nodes
    result = dag.execute(today, cache, krx_client=krx, is_single=True)
    
    final_df = result.outputs["n6"]
    
    # Challenge criteria: RS >= 70
    candidates = final_df[final_df["rs_rating"] >= 70].copy()
    
    # Sort by total_score
    top_10 = candidates.sort_values("total_score", ascending=False).head(10)
    
    with open("data/results/challenge_watchlist.md", "w") as f:
        f.write("# 🏆 AlphaForge vs Perplexity 공식 챌린지\n")
        f.write(f"분석 일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write("대상 일자: 2026-05-11 (월요일)\n\n")
        f.write("이 리스트는 하락장 속에서도 기술적 패턴과 수급이 살아있는 **'월요일의 주역'** 후보들입니다.\n")
        f.write("퍼플렉시티의 검증 요청에 따라 미리 기록해두는 타율 측정용 리스트입니다.\n\n")
        f.write("| 순위 | 종목명 | 코드 | 총점 | RS | VCP | 상태 |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for i, (idx, row) in enumerate(top_10.iterrows(), 1):
            f.write(f"| {i} | {row['name']} | {row['code']} | {row['total_score']} | {row['rs_rating']:.1f} | {row['vcp_score']} | {row.get('vcp_warning', '')} |\n")
            
    print("Challenge Watchlist created.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(generate_challenge())
