"""
DAG 실행 API — 프론트엔드에서 호출하여 실제 KRX 데이터로 파이프라인을 실행합니다.
"""
import asyncio
import json
import logging
from datetime import datetime

import math
import pandas as pd
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.analysis_jobs import analysis_jobs
from backend.analysis_summary import build_analysis_payload
from backend.alphaforge_export import (
    export_alphaforge_candidates,
    export_alphaforge_daily_history,
    export_alphaforge_dual_horizon,
    format_dual_horizon_console,
)
from backend.alphaforge_policy import build_ai_system_prompt, policy_metadata, validate_ai_provider
from data.naver_krx import NaverKRXClient
from data.holidays import prev_trading_day, is_trading_day
from engine.cache import ResultCache
from engine.dag import DAG
from nodes import NODE_REGISTRY

logger = logging.getLogger(__name__)
router = APIRouter()


from backend.utils.json_safety import sanitize_for_json, validate_json_safety


def _build_pipeline_diagnostics(krx, result, node_results: dict) -> dict:
    """파이프라인 결측/지연/소스 분포 진단을 반환합니다.

    - ohlcv: per-code 결측, OK 비율, 결측 종목 샘플
    - node_latency_top: 시간 소비 상위 노드
    - status_distribution: vcp_status, breakout_status, rs_status, liquidity_status, ma_alignment_flag 분포
    - volume_suspicious_count: 거래량 의심 종목 개수
    - data_insufficient_count: 데이터 부족 사유별 종목 수
    """
    diagnostics: dict = {}

    # 1. OHLCV 진단 (NaverKRXClient에서 누적된 per-code 정보)
    try:
        ohlcv_diag = krx.get_ohlcv_diagnostics()
        diagnostics["ohlcv"] = ohlcv_diag
    except Exception as exc:
        diagnostics["ohlcv"] = {"error": str(exc)}

    # 2. 노드별 지연 (상위 5개)
    try:
        latencies = sorted(
            [(log.node_id, round(log.latency_ms, 1)) for log in result.node_logs],
            key=lambda x: x[1], reverse=True,
        )
        diagnostics["node_latency_top"] = [
            {"node_id": nid, "latency_ms": ms} for nid, ms in latencies[:5]
        ]
        diagnostics["total_latency_ms"] = round(getattr(result, "total_latency_ms", 0), 1)
    except Exception as exc:
        diagnostics["node_latency_top"] = []
        diagnostics["latency_error"] = str(exc)

    # 3. 상태 분포 — 마지막(score_filter) 노드 결과 기준
    try:
        # score_filter 또는 마지막 노드 찾기
        target_node = None
        for log in result.node_logs:
            if log.node_type == "score_filter" and log.node_id in result.outputs:
                target_node = log.node_id
                break
        if target_node is None and result.node_logs:
            for log in reversed(result.node_logs):
                if log.node_id in result.outputs:
                    target_node = log.node_id
                    break

        if target_node and target_node in result.outputs:
            df = result.outputs[target_node]
            distributions = {}
            volume_suspicious_count = 0
            data_insufficient_reasons: dict[str, int] = {}

            for col in (
                "vcp_status", "breakout_status", "rs_status", "liquidity_status", "ma_alignment_flag",
                "vcp_width_trend", "vcp_volume_trend", "vcp_confidence",
                "watch_alert_type", "candidate_confidence", "rs_freshness_status"
            ):
                if col in df.columns:
                    vc = df[col].fillna("MISSING").astype(str).value_counts().to_dict()
                    distributions[col] = vc

            if "volume_suspicious" in df.columns:
                volume_suspicious_count = int(df["volume_suspicious"].fillna(False).sum())

            if "watch_alert_type" in df.columns:
                diagnostics["action_alert_count"] = int(df["watch_alert_type"].eq("ACTION_ALERT").sum())
                diagnostics["risk_watch_count"] = int(df["watch_alert_type"].eq("RISK_WATCH").sum())

            # data_insufficient 사유 집계
            for col_name, reason_label in [
                ("vcp_status", "VCP DATA_MISSING"),
                ("breakout_status", "BREAKOUT DATA_MISSING"),
                ("rs_status", "RS DATA_MISSING"),
                ("ma_alignment_flag", "MA DATA_MISSING"),
                ("rs_freshness_status", "RS_STALE_UNEXPECTED"),
            ]:
                if col_name in df.columns:
                    if col_name == "rs_freshness_status":
                        cnt = int((df[col_name].astype(str) == "STALE_UNEXPECTED").sum())
                    else:
                        cnt = int((df[col_name].astype(str) == "DATA_MISSING").sum())
                    if cnt > 0:
                        data_insufficient_reasons[reason_label] = cnt
            if "liquidity_status" in df.columns:
                cnt = int(df["liquidity_status"].astype(str).isin(
                    ["DATA_MISSING", "LIQUIDITY_UNKNOWN", "LIQUIDITY_UNCERTAIN"]
                ).sum())
                if cnt > 0:
                    data_insufficient_reasons["LIQUIDITY DATA_INSUFFICIENT"] = cnt

            diagnostics["status_distribution"] = distributions
            diagnostics["volume_suspicious_count"] = volume_suspicious_count
            diagnostics["data_insufficient_reasons"] = data_insufficient_reasons
            diagnostics["total_rows"] = int(len(df))
    except Exception as exc:
        diagnostics["status_distribution_error"] = str(exc)

    return diagnostics


def _execute_sync(
    body: dict,
    target_code: str = None,
    is_single: bool = False,
    progress_callback=None,
) -> dict:
    """동기 실행 함수 (스레드풀에서 실행됨)."""
    # [신규] 매 요청 시 메모리 캐시 초기화 (데이터 신선도 유지 및 메모리 팽창 방지)
    from data.naver_krx import _OHLCV_MEM_CACHE
    _OHLCV_MEM_CACHE.clear()

    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    cache = ResultCache(cache_dir=settings.data_cache_dir)

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # [Perplexity 제안 반영] 심야 시간대(23:30 ~ 01:00) 데이터 불안정 대응
    if is_trading_day(today):
        if now.hour >= 23 and now.minute >= 30:
            as_of_date = today
        else:
            as_of_date = today
    else:
        as_of_date = prev_trading_day(today, n=1)

    dag = DAG()

    raw_nodes = body.get("nodes", [])
    raw_edges = body.get("edges", [])
    max_symbols = body.get("max_symbols")
    try:
        max_symbols = int(max_symbols) if max_symbols not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        max_symbols = None

    # 연결된 노드만 포함 (고아 노드 제외)
    edge_node_ids = set()
    for ed in raw_edges:
        edge_node_ids.add(ed["from"])
        edge_node_ids.add(ed["to"])

    # 노드 등록
    nodes_map = {}
    for nd in raw_nodes:
        ntype, nid = nd["type"], nd["id"]
        if ntype not in NODE_REGISTRY:
            continue
        cls = NODE_REGISTRY[ntype]
        inst = cls()
        if inst.INPUT_ARITY > 0 and nid not in edge_node_ids:
            continue

        node_params = nd.get("params", {})
        if ntype == "universe" and target_code:
            node_params["manual_codes"] = [target_code]
            node_params["market"] = "ALL"
        elif ntype == "universe" and max_symbols:
            node_params["max_symbols"] = max_symbols

        nodes_map[nid] = inst
        dag.add_node(nid, inst, node_params)

    # 엣지 등록
    for ed in raw_edges:
        fid, tid = ed["from"], ed["to"]
        if fid not in nodes_map or tid not in nodes_map:
            continue
        dag.add_edge(fid, tid)

    # 실행
    result = dag.execute(
        as_of_date,
        cache,
        krx_client=krx,
        is_single=is_single,
        progress_callback=progress_callback,
    )

    # 응답 조립
    node_results = {}
    for log in result.node_logs:
        nr = {
            "status": log.status,
            "node_id": log.node_id,
            "node_type": log.node_type,
            "output_count": log.output_count,
            "input_count": log.input_count,
            "latency_ms": round(log.latency_ms, 1),
            "cache_hit": log.cache_hit,
            "dropped_count": getattr(log, "dropped_count", max(log.input_count - log.output_count, 0)),
            "drop_reasons": getattr(log, "drop_reasons", []),
            "data_missing_count": getattr(log, "data_missing_count", 0),
            "data_missing_ratio": getattr(log, "data_missing_ratio", 0.0),
            "nan_columns": getattr(log, "nan_columns", []),
            "error": log.error,
            "data": [],
            "columns": [],
            "total_count": 0,
        }
        if log.node_id in result.outputs:
            df = result.outputs[log.node_id]
            from backend.alphaforge_policy import normalize_result_schema
            nr["columns"] = list(df.columns)
            rows = df.head(200).where(pd.notna(df), None).to_dict(orient="records")
            nr["data"] = [normalize_result_schema(r) for r in rows]
            nr["total_count"] = len(df)
        node_results[log.node_id] = nr

    # [신규] OHLCV/유동성/노드 진단 집계 — 결측 분포 표시
    pipeline_diagnostics = _build_pipeline_diagnostics(krx, result, node_results)

    payload = {
        "success": result.success,
        "error": result.error,
        "as_of_date": as_of_date,
        "run_id": result.run_id,
        "policy": policy_metadata(),
        "node_results": node_results,
        "pipeline_diagnostics": pipeline_diagnostics,
    }
    payload.update(build_analysis_payload(result, node_results, as_of_date=as_of_date))

    if result.success and not is_single:
        try:
            final_df = None
            for log in result.node_logs:
                if log.node_type == "score_filter" and log.node_id in result.outputs:
                    final_df = result.outputs[log.node_id]
                    break
            if final_df is None and result.node_logs:
                for log in reversed(result.node_logs):
                    if log.node_id in result.outputs:
                        final_df = result.outputs[log.node_id]
                        break
            final_export_df = final_df if final_df is not None else pd.DataFrame()
            generated_at = datetime.now().isoformat(timespec="seconds")

            from backend.performance_tracker import save_snapshots
            save_snapshots(final_export_df, as_of_date)

            export_count = export_alphaforge_candidates(final_export_df, generated_at=generated_at)
            logger.info("AlphaForge 후보 export 완료: %s개", export_count)
            dual_count = export_alphaforge_dual_horizon(final_export_df)
            logger.info("AlphaForge dual horizon export 완료: %s개", dual_count)
            print(format_dual_horizon_console(final_export_df))
            market = "ALL"
            for nd in raw_nodes:
                if nd.get("type") == "universe":
                    market = str((nd.get("params") or {}).get("market") or market)
                    break
            history_count = export_alphaforge_daily_history(
                final_export_df,
                run_date=as_of_date,
                generated_at=generated_at,
                market=market,
                universe_count=payload.get("summary", {}).get("universe_count"),
            )
            logger.info("AlphaForge daily history 저장 완료: %s개", history_count)
        except Exception as e:
            logger.error("AlphaForge 후보 export 실패: %s", e)

    # [신규] 데일리 분석 결과 자동 기록 (data/results/ 폴더)
    if result.success and not is_single:
        try:
            import os
            timestamp = datetime.now().strftime("%H%M%S")
            filename = f"screening_{as_of_date}_{timestamp}.json"
            save_path = os.path.join("data", "results", filename)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)

            # [FIX] 저장 전 반드시 JSON 안전화 처리
            sanitized_payload = sanitize_for_json(payload)
            validate_json_safety(sanitized_payload, context=f"File Storage: {filename}")

            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(sanitized_payload, f, ensure_ascii=False, indent=2)
            logger.info("데일리 분석 결과 저장 완료: %s", save_path)
        except Exception as e:
            logger.error("분석 결과 저장 실패: %s", e)

    return payload


_SIGNAL_LABELS: dict[str, str] = {
    "vcp":              "VCP 변동성 수축 패턴",
    "box_breakout":     "박스권 돌파",
    "ma_alignment":     "이평선 정배열(5>20>60)",
    "foreign_flow":     "외국인 순매수",
    "institution_flow": "기관 순매수",
    "liquidity_filter": "유동성 필터",
    "score_filter":     "최종 점수 및 리스크 게이트",
}


def _run_ai_analysis(stocks: list[dict], api_key: str, provider: str, signals: list[str], as_of_date: str) -> dict:
    provider = validate_ai_provider(provider)
    signal_names = [_SIGNAL_LABELS.get(s, s) for s in signals]
    system_prompt = build_ai_system_prompt(as_of_date=as_of_date, signal_names=signal_names)

    from llm.gemini import gemini_analyze_stocks_with_key
    comments, cost = gemini_analyze_stocks_with_key(stocks, api_key, system_prompt=system_prompt)
    return {"comments": comments, "cost_usd": cost, "policy": policy_metadata(), "provider": provider}


@router.post("/api/ai_comment")
async def ai_comment(request: Request):
    """AI 종목 분석 — 프론트에서 직접 API 키 제공."""
    body = await request.json()
    stocks   = body.get("stocks", [])[:30]
    api_key  = body.get("api_key", "").strip()
    provider = body.get("provider", "gemini")
    signals  = body.get("signals", [])

    today = datetime.now().strftime("%Y-%m-%d")
    as_of_date = body.get("as_of_date") or (today if is_trading_day(today) else prev_trading_day(today, n=1))

    if not stocks:
        return JSONResponse({"comments": {}, "cost_usd": 0.0})
    if not api_key:
        return JSONResponse({"error": "API 키가 필요합니다."}, status_code=400)

    try:
        result = await asyncio.to_thread(_run_ai_analysis, stocks, api_key, provider, signals, as_of_date)
        return JSONResponse(sanitize_for_json(result))
    except Exception as exc:
        logger.exception("AI 분석 실패")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/execute")
async def execute_dag(request: Request):
    body = await request.json()
    try:
        logger.warning("/api/execute는 호환성용 동기 실행입니다. 웹 UI는 /api/analysis/jobs를 사용하세요.")
        result = await asyncio.to_thread(_execute_sync, body)
        return JSONResponse(sanitize_for_json(result))
    except Exception as exc:
        logger.exception("DAG 실행 실패")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/api/analysis/jobs")
async def create_analysis_job(request: Request):
    """전체 분석을 백그라운드 job으로 시작하고 즉시 job_id를 반환합니다."""
    body = await request.json()

    def runner(progress_callback):
        return _execute_sync(body, progress_callback=progress_callback)

    job = analysis_jobs.create(runner)
    return JSONResponse({"job_id": job.job_id, "status": job.status})


@router.get("/api/analysis/jobs/{job_id}")
async def get_analysis_job(job_id: str):
    job = analysis_jobs.get(job_id)
    if job is None:
        return JSONResponse({"error": "분석 작업을 찾을 수 없습니다."}, status_code=404)
    return JSONResponse(sanitize_for_json(job.summary()))


@router.get("/api/analysis/jobs/{job_id}/result")
async def get_analysis_job_result(job_id: str):
    job = analysis_jobs.get(job_id)
    if job is None:
        return JSONResponse({"error": "분석 작업을 찾을 수 없습니다."}, status_code=404)
    if job.status == "failed":
        return JSONResponse({"success": False, "error": job.error}, status_code=500)
    if job.status != "completed" or job.result is None:
        return JSONResponse({"error": "분석이 아직 완료되지 않았습니다.", "status": job.status}, status_code=202)

    # [FIX] 응답 전 반드시 JSON 안전화 처리
    sanitized_result = sanitize_for_json(job.result)
    validate_json_safety(sanitized_result, context=f"API Response: {job_id}")
    return JSONResponse(sanitized_result)


@router.post("/api/analyze_single_stock")
async def analyze_single_stock(request: Request):
    """특정 종목 1개만 정밀 분석."""
    body = await request.json()
    query = body.get("query", "").strip()
    dag_config = body.get("dag_config", {})

    if not query:
        return JSONResponse({"error": "종목명 또는 코드가 필요합니다."}, status_code=400)

    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    as_of_date = today if is_trading_day(today) else prev_trading_day(today, n=1)

    # 1. 종목 코드/이름 확인 (정교한 매칭)
    # [최적화] 이미 6자리 코드면 유니버스 전체 페치 없이 즉시 진행 시도
    import re
    if re.match(r'^\d{6}$', query):
        target_code = query
        # 최소한의 유니버스 정보를 위해 get_universe 호출 (캐시 활용됨)
        universe = krx.get_universe(as_of_date, manual_codes=[target_code])
        target = universe[universe["code"] == target_code]
    else:
        universe = krx.get_universe(as_of_date)
        # 순위 1: 코드 완전 일치
        target = universe[universe["code"] == query]

    # 순위 2: 이름 완전 일치 (대소문자 무시)
    if target.empty:
        target = universe[universe["name"].str.upper() == query.upper()]

    # 순위 3: 이름 포함 관계 (양방향 + 대소문자 무시)
    if target.empty:
        query_up = query.upper()
        target = universe[
            universe["name"].apply(lambda x: x.upper() in query_up or query_up in x.upper())
        ]
        if not target.empty:
            target = target.sort_values("market_cap", ascending=False).head(1)

    # 순위 4: [지능형 Fallback] 한글-영문 주요 매핑 (네이버 -> NAVER 등)
    if target.empty:
        alias_map = {"네이버": "NAVER", "엔씨": "엔씨소프트", "하닉": "SK하이닉스", "포홀": "POSCO홀딩스"}
        if query in alias_map:
            target = universe[universe["name"] == alias_map[query]]

    if target.empty:
        return JSONResponse({"error": f"종목을 찾을 수 없습니다: {query}"}, status_code=404)

    target_code = target.iloc[0]["code"]
    target_name = target.iloc[0]["name"]

    try:
        # 2. 분석 실행 (target_code 및 is_single=True 주입)
        result = await asyncio.to_thread(_execute_sync, dag_config, target_code, is_single=True)
        result["target_name"] = target_name
        result["target_code"] = target_code
        return JSONResponse(sanitize_for_json(result))
    except Exception as exc:
        logger.exception("단일 종목 분석 실패")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ── 타율 추적 엔드포인트 ─────────────────────────────────────────────────

@router.get("/api/performance")
async def get_performance():
    """저장된 타율 데이터를 반환합니다."""
    from data.tracker.tracker import load_performance
    return JSONResponse(load_performance())


@router.post("/api/performance/update")
async def update_performance():
    """스냅샷 재수집 + 수익률 계산 + 저장을 실행합니다."""
    from data.tracker.tracker import run_tracker_update
    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    try:
        perf = await asyncio.to_thread(run_tracker_update, krx)
        return JSONResponse({"success": True, "total_records": perf["total_records"]})
    except Exception as exc:
        logger.exception("타율 업데이트 실패")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
