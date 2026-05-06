"""
DAG 실행 API — 프론트엔드에서 호출하여 실제 KRX 데이터로 파이프라인을 실행합니다.
"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.config import settings
from data.naver_krx import NaverKRXClient
from data.holidays import prev_trading_day, is_trading_day
from engine.cache import ResultCache
from engine.dag import DAG
from nodes import NODE_REGISTRY

logger = logging.getLogger(__name__)
router = APIRouter()


def _execute_sync(body: dict) -> dict:
    """동기 실행 함수 (스레드풀에서 실행됨)."""
    krx = NaverKRXClient(cache_dir=settings.data_cache_dir)
    cache = ResultCache(cache_dir=settings.data_cache_dir)

    today = datetime.now().strftime("%Y-%m-%d")
    as_of_date = today if is_trading_day(today) else prev_trading_day(today, n=1)

    dag = DAG()

    raw_nodes = body.get("nodes", [])
    raw_edges = body.get("edges", [])

    # 연결된 노드만 포함 (고아 노드 제외)
    edge_node_ids = set()
    for ed in raw_edges:
        edge_node_ids.add(ed["from"])
        edge_node_ids.add(ed["to"])

    # 노드 등록: source(arity=0)는 항상 포함, 나머지는 엣지에 참여하는 것만
    nodes_map = {}
    for nd in raw_nodes:
        ntype, nid = nd["type"], nd["id"]
        if ntype not in NODE_REGISTRY:
            continue  # 알 수 없는 타입은 건너뜀
        cls = NODE_REGISTRY[ntype]
        inst = cls()
        if inst.INPUT_ARITY > 0 and nid not in edge_node_ids:
            continue  # 연결 안 된 비-소스 노드 건너뜀
        nodes_map[nid] = inst
        dag.add_node(nid, inst, nd.get("params", {}))

    # 엣지 등록 (양쪽 노드가 모두 등록된 것만, 슬롯은 자동 할당)
    for ed in raw_edges:
        fid, tid = ed["from"], ed["to"]
        if fid not in nodes_map or tid not in nodes_map:
            continue
        dag.add_edge(fid, tid)

    # 실행
    result = dag.execute(as_of_date, cache, krx_client=krx)

    # 응답 조립
    node_results = {}
    for log in result.node_logs:
        nr = {
            "status": log.status,
            "output_count": log.output_count,
            "input_count": log.input_count,
            "latency_ms": round(log.latency_ms, 1),
            "cache_hit": log.cache_hit,
            "error": log.error,
            "data": [],
            "columns": [],
            "total_count": 0,
        }
        if log.node_id in result.outputs:
            df = result.outputs[log.node_id]
            nr["columns"] = list(df.columns)
            # 기존 30개에서 200개로 대폭 확장 (우량주 누락 방지)
            nr["data"] = json.loads(df.head(200).to_json(orient="records", force_ascii=False))
            nr["total_count"] = len(df)
        node_results[log.node_id] = nr

    return {
        "success": result.success,
        "as_of_date": as_of_date,
        "run_id": result.run_id,
        "node_results": node_results,
    }


_SIGNAL_LABELS: dict[str, str] = {
    "vcp":              "VCP 변동성 수축 패턴",
    "box_breakout":     "박스권 돌파",
    "ma_alignment":     "이평선 정배열(5>20>60)",
    "foreign_flow":     "외국인 순매수",
    "institution_flow": "기관 순매수",
    "liquidity_filter": "유동성 필터",
    "score_filter":     "종가 필터",
}


def _run_ai_analysis(stocks: list[dict], api_key: str, provider: str, signals: list[str]) -> dict:
    signal_names = [_SIGNAL_LABELS.get(s, s) for s in signals]
    system_prompt = (
        f"당신은 한국 주식 전문 애널리스트입니다. "
        f"다음 조건으로 스크리닝된 종목들을 분석합니다: {', '.join(signal_names)}. "
        f"분석 시 반드시 '박스권 돌파 배수(X.X배)'와 'VCP 수축 거래량 백분율(XX.X%)' 등 정량적 수치를 근거로 포함하세요. "
        f"각 종목의 팩터 값을 근거로 매매 관점의 간결한 분석을 제공하세요. "
        f"투자 권유가 아닌 참고 분석임을 명심하세요."
    )
    if provider == "claude":
        from llm.claude import claude_analyze_stocks
        comments, cost = claude_analyze_stocks(stocks, api_key, system_prompt=system_prompt)
    else:
        from llm.gemini import gemini_analyze_stocks_with_key
        comments, cost = gemini_analyze_stocks_with_key(stocks, api_key, system_prompt=system_prompt)
    return {"comments": comments, "cost_usd": cost}


@router.post("/api/ai_comment")
async def ai_comment(request: Request):
    """AI 종목 분석 — 프론트에서 직접 API 키 제공."""
    body = await request.json()
    stocks   = body.get("stocks", [])[:30]
    api_key  = body.get("api_key", "").strip()
    provider = body.get("provider", "gemini")
    signals  = body.get("signals", [])

    if not stocks:
        return JSONResponse({"comments": {}, "cost_usd": 0.0})
    if not api_key:
        return JSONResponse({"error": "API 키가 필요합니다."}, status_code=400)

    try:
        result = await asyncio.to_thread(_run_ai_analysis, stocks, api_key, provider, signals)
        return JSONResponse(result)
    except Exception as exc:
        logger.exception("AI 분석 실패")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/execute")
async def execute_dag(request: Request):
    body = await request.json()
    try:
        result = await asyncio.to_thread(_execute_sync, body)
        return JSONResponse(result)
    except Exception as exc:
        logger.exception("DAG 실행 실패")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
