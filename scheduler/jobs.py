"""
APScheduler 기반 스케줄링 모듈.
"""
import asyncio
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


async def scheduled_flow_refresh() -> None:
    """평일 18:00/18:30 KST — KRX 확정 수급 데이터 재수집.

    장 마감 후 KRX·Naver가 당일 집계를 완료하는 시점(보통 18:00 전후)에
    수급 데이터를 갱신합니다. 브라우저에서 재실행 시 당일 확정값이 표시됩니다.
    KOSDAQ 종목은 19:00 이후 완료될 수 있으므로 18:30에 한 번 더 시도합니다.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    if not is_trading_day(today_str):
        return

    logger.info("[수급 갱신] KRX 확정 수급 재수집 시작: %s", today_str)
    try:
        from pathlib import Path
        import glob

        # 오늘 날짜 기준 수급 캐시 파일 삭제 → 다음 실행 시 새로 수집
        cache_dir = Path(settings.data_cache_dir)
        stale_patterns = [
            f"foreign_flow_daily_{today_str}*",
            f"institution_flow_daily_{today_str}*",
        ]
        removed = 0
        for pattern in stale_patterns:
            for f in cache_dir.glob(pattern):
                f.unlink(missing_ok=True)
                removed += 1

        if removed > 0:
            logger.info("[수급 갱신] 당일 수급 캐시 %d개 삭제 완료 — 다음 실행 시 재수집됩니다", removed)
        else:
            logger.info("[수급 갱신] 삭제할 캐시 없음 (이미 갱신됐거나 캐시 미생성)")

    except Exception as exc:
        logger.exception("[수급 갱신] 캐시 삭제 실패: %s", exc)


async def scheduled_tracker_update() -> None:
    """평일 19:00 KST — 전일 스크리닝 종목의 당일 수익률 계산 및 타율 집계."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    if not is_trading_day(today_str):
        return
    logger.info("[타율 추적] 업데이트 시작: %s", today_str)
    try:
        from data.tracker.tracker import run_tracker_update
        krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
        perf = await asyncio.get_event_loop().run_in_executor(None, run_tracker_update, krx)
        logger.info("[타율 추적] 완료 — 총 %d건, Tier1 d1타율: %s",
                    perf.get("total_records", 0),
                    perf.get("d1_summary", {}).get("tier1", {}).get("hit_rate"))
    except Exception as exc:
        logger.exception("[타율 추적] 실패: %s", exc)


def start_scheduler() -> None:
    """스케줄러 시작 및 Job 등록."""
    # 14:30 — 정규 파이프라인 (T-1 확정 데이터 기준)
    scheduler.add_job(
        scheduled_pipeline_run,
        trigger="cron",
        day_of_week="mon-fri",
        hour=14,
        minute=30,
        id="daily_pipeline",
        replace_existing=True,
    )
    # 18:00 — KRX 당일 수급 확정 후 캐시 갱신 (KOSPI 기준)
    scheduler.add_job(
        scheduled_flow_refresh,
        trigger="cron",
        day_of_week="mon-fri",
        hour=18,
        minute=0,
        id="flow_refresh_1800",
        replace_existing=True,
    )
    # 18:30 — 재시도 (KOSDAQ 집계 지연 대응)
    scheduler.add_job(
        scheduled_flow_refresh,
        trigger="cron",
        day_of_week="mon-fri",
        hour=18,
        minute=30,
        id="flow_refresh_1830",
        replace_existing=True,
    )
    # 19:00 — 타율 추적 업데이트 (수급 갱신 완료 후)
    scheduler.add_job(
        scheduled_tracker_update,
        trigger="cron",
        day_of_week="mon-fri",
        hour=19,
        minute=0,
        id="tracker_update_1900",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("스케줄러 시작 완료 (월~금 14:30 파이프라인 / 18:00·18:30 수급 갱신 / 19:00 타율 KST)")


def stop_scheduler() -> None:
    """스케줄러 종료."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("스케줄러 종료")
