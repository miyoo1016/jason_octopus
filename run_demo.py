"""
AlphaForge 통합 데모 스크립트.
데이터 수집(네이버 증권), DAG 파이프라인, 백테스트를 순서대로 실행합니다.
"""
import asyncio
import logging
import sys
from datetime import datetime

from backend.config import settings
from data.naver_krx import NaverKRXClient
from data.holidays import prev_trading_day
from engine.cache import ResultCache
from engine.dag import DAG
from backtest.engine import BacktestEngine, BacktestParams
import nodes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run_pipeline_demo() -> None:
    logger.info("=== AlphaForge 파이프라인 데모 시작 ===")

    krx   = NaverKRXClient(cache_dir=settings.data_cache_dir)
    cache = ResultCache(cache_dir=settings.data_cache_dir)

    today      = datetime.now().strftime("%Y-%m-%d")
    as_of_date = prev_trading_day(today, n=1)
    logger.info("기준일: %s", as_of_date)

    # ── 1. DAG 구성 ──────────────────────────────────────────────────────────
    dag = DAG(name="demo")
    dag.add_node("universe",     nodes.UniverseNode(),        {"market": "KOSPI"})
    dag.add_node("foreign",      nodes.ForeignFlowNode(),     {"n_days": 5})
    dag.add_node("institution",  nodes.InstitutionFlowNode(), {"n_days": 5})
    dag.add_node("price_filter", nodes.ScoreFilterNode(),     {"score_column": "close", "threshold": 50000, "greater_than": True})
    dag.add_node("top_n",        nodes.TopNNode(),            {"sort_column": "foreign_net_buy", "ascending": False, "n": 5})

    dag.add_edge("universe",    "foreign")
    dag.add_edge("foreign",     "institution")
    dag.add_edge("institution", "price_filter")
    dag.add_edge("price_filter","top_n")

    # ── 2. 파이프라인 실행 ───────────────────────────────────────────────────
    result = dag.execute(as_of_date, cache, krx_client=krx)

    if not result.success:
        failed = [log.node_id for log in result.node_logs if log.status == "error"]
        logger.error("파이프라인 실패 노드: %s", failed)
        return

    final_df = result.outputs["top_n"]
    logger.info("파이프라인 결과: %d 종목 (%.0fms)", len(final_df), result.total_latency_ms)

    display_cols = [c for c in ["code", "name", "close", "foreign_net_buy", "institution_net_buy"] if c in final_df.columns]
    print("\n[파이프라인 결과]")
    print(final_df[display_cols].to_string(index=False))

    # ── 3. 노드별 실행 로그 출력 ─────────────────────────────────────────────
    print("\n[노드 실행 로그]")
    for log in result.node_logs:
        status_icon = {"ok": "✅", "cache_hit": "💾", "error": "❌", "skipped": "⏭"}.get(log.status, "?")
        print(f"  {status_icon} {log.node_id:15s} | {log.output_count:4d}행 | {log.latency_ms:6.0f}ms | {log.status}")

    # ── 4. 백테스트 ──────────────────────────────────────────────────────────
    # 30일 전 시그널 날짜를 사용 → entry(T+1)~청산(T+11) 데이터가 이미 존재함
    if not final_df.empty:
        logger.info("\n=== 백테스트 엔진 구동 ===")
        from data.holidays import prev_trading_day as _prev
        signal_date = _prev(as_of_date, n=30)
        logger.info("백테스트 시그널 날짜: %s (30거래일 전)", signal_date)
        bt_engine = BacktestEngine(krx)
        bt_params = BacktestParams(hold_days=10, stop_loss_pct=0.05, take_profit_pct=0.15)
        bt_result = bt_engine.run_signal_backtest(final_df, signal_date, bt_params)

        print(f"\n[백테스트 결과]")
        print(f"  매매횟수: {bt_result['trade_count']}회")
        print(f"  승률:     {bt_result['win_rate']:.1f}%")
        print(f"  평균수익: {bt_result['total_return']:.2f}%")
        print(f"  최대낙폭: {bt_result['mdd']:.2f}%")

    logger.info("=== 데모 종료 ===")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_pipeline_demo())
