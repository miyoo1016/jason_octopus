from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import pandas as pd
import json

router = APIRouter()
templates = Jinja2Templates(directory="frontend")

@router.get("/report/screen", response_class=HTMLResponse)
async def render_screen_report(request: Request, as_of_date: str = "2026-05-05", node_id: str = "결과"):
    # 데모용 더미 데이터
    df = pd.DataFrame([
        {"code": "005930", "name": "삼성전자", "close": 80000, "score": 95},
        {"code": "000660", "name": "SK하이닉스", "close": 150000, "score": 92},
    ])
    
    return templates.TemplateResponse(
        "templates/report_screen.html",
        {
            "request": request,
            "as_of_date": as_of_date,
            "node_id": node_id,
            "columns": list(df.columns),
            "rows": df.to_dict(orient="records"),
            "row_count": len(df),
            "latency_ms": 150.5
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
