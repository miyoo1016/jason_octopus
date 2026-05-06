"""
APScheduler 기반 스케줄링 모듈.
"""
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.config import settings
from data.holidays import is_trading_day, prev_trading_day
from data.naver_krx import NaverKRXClient
from engine.cache import ResultCache
from engine.dag import DAG
from nodes import (
    UniverseNode, ForeignFlowNode, InstitutionFlowNode,
    ScoreFilterNode, TopNNode,
)
from notify.telegram import send_telegram_message, format_results_to_markdown

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


def _build_default_dag() -> DAG:
    """기본 전략 DAG: 유니버스 → 외국인수급 → 기관수급 → 가격필터 → Top20."""
    dag = DAG(name="daily_default")
    dag.add_node("universe",     UniverseNode(),        {"market": "ALL"})
    dag.add_node("foreign",      ForeignFlowNode(),     {"n_days": 5})
    dag.add_node("institution",  InstitutionFlowNode(), {"n_days": 5})
    dag.add_node("price_filter", ScoreFilterNode(),     {"score_column": "close", "threshold": 5000, "greater_than": True})
    dag.add_node("top20",        TopNNode(),            {"sort_column": "foreign_net_buy", "ascending": False, "n": 20})

    dag.add_edge("universe",    "foreign")
    dag.add_edge("foreign",     "institution")
    dag.add_edge("institution", "price_filter")
    dag.add_edge("price_filter","top20")
    return dag


async def scheduled_pipeline_run() -> None:
    """평일 14:30 KST에 실행되는 메인 파이프라인."""
    today_str = datetime.now().strftime("%Y-%m-%d")

    if not is_trading_day(today_str):
        logger.info("휴장일 (%s) — 실행 건너뜀", today_str)
        return

    logger.info("정규 파이프라인 실행 시작: %s", today_str)

    try:
        as_of_date = prev_trading_day(today_str, n=1)
        krx   = NaverKRXClient(cache_dir=settings.data_cache_dir)
        cache = ResultCache(cache_dir=settings.data_cache_dir)
        dag   = _build_default_dag()

        result = dag.execute(as_of_date, cache, krx_client=krx)

        if not result.success:
            failed = [log.node_id for log in result.node_logs if log.status == "error"]
            raise RuntimeError(f"DAG 실행 실패 노드: {failed}")

        final_df = result.outputs.get("top20")
        if final_df is None or final_df.empty:
            msg = f"📊 *AlphaForge {today_str}*\n\n조건을 만족하는 종목이 없습니다."
        else:
            msg = format_results_to_markdown(
                final_df,
                title=f"AlphaForge KR — {today_str} (외국인 순매수 Top20)",
            )

        latency = round(result.total_latency_ms / 1000, 1)
        msg += f"\n\n⏱ 총 처리: {latency}s | 캐시 히트율: {result.cache_hit_rate:.0%}"

        await send_telegram_message(msg)
        logger.info("파이프라인 완료 — %d종목, %.1fs", len(final_df) if final_df is not None else 0, latency)

    except Exception as exc:
        logger.exception("파이프라인 실행 오류")
        await send_telegram_message(f"❌ AlphaForge 오류 ({today_str}): {exc}")


def start_scheduler() -> None:
    """스케줄러 시작 및 Job 등록."""
    scheduler.add_job(
        scheduled_pipeline_run,
        trigger="cron",
        day_of_week="mon-fri",
        hour=14,
        minute=30,
        id="daily_pipeline",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("스케줄러 시작 완료 (월~금 14:30 KST)")


def stop_scheduler() -> None:
    """스케줄러 종료."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("스케줄러 종료")
