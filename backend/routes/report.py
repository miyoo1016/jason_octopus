from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import pandas as pd
import json
from pathlib import Path

router = APIRouter()
templates = Jinja2Templates(directory="frontend")


def _load_latest_screening_result() -> tuple[str, str, pd.DataFrame, float]:
    """저장된 스크리닝 결과 중 최신 최종 노드 데이터를 로드합니다."""
    results_dir = Path("data/results")
    files = sorted(results_dir.glob("screening_*.json"))
    for path in reversed(files):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        node_results = raw.get("results", {})
        final_node_id = ""
        final_node = None
        for node_id, node in node_results.items():
            if node.get("data"):
                final_node_id = node_id
                final_node = node

        if final_node is None:
            continue

        df = pd.DataFrame(final_node.get("data", []))
        latency_ms = sum(float(n.get("latency_ms", 0) or 0) for n in node_results.values())
        return raw.get("as_of_date", ""), final_node_id, df, latency_ms

    return "", "결과", pd.DataFrame(), 0.0

@router.get("/report/screen", response_class=HTMLResponse)
async def render_screen_report(request: Request):
    as_of_date, node_id, df, latency_ms = _load_latest_screening_result()
    
    return templates.TemplateResponse(
        "templates/report_screen.html",
        {
            "request": request,
            "as_of_date": as_of_date or "저장된 결과 없음",
            "node_id": node_id,
            "columns": list(df.columns),
            "rows": df.to_dict(orient="records"),
            "row_count": len(df),
            "latency_ms": latency_ms,
        }
    )

@router.get("/report/backtest", response_class=HTMLResponse)
async def render_backtest_report(request: Request):
    # 데모용 더미 데이터
    trades = [
        {"entry_date": "2026-01-05", "exit_date": "2026-01-20", "code": "005930", "name": "삼성전자", "entry_price": 75000, "exit_price": 82000, "return_pct": 9.33},
        {"entry_date": "2026-02-10", "exit_date": "2026-02-15", "code": "000660", "name": "SK하이닉스", "entry_price": 140000, "exit_price": 135000, "return_pct": -3.57},
    ]
    
    return templates.TemplateResponse(
        "templates/report_backtest.html",
        {
            "request": request,
            "strategy_name": "VCP + 외국인 수급",
            "start_date": "2026-01-01",
            "end_date": "2026-05-05",
            "total_return": 15.4,
            "win_rate": 65.0,
            "mdd": -8.5,
            "trade_count": 25,
            "trades": trades
        }
    )
