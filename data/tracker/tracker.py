"""
AlphaForge 실전 타율 추적 모듈.

흐름:
  1. data/results/screening_YYYY-MM-DD_*.json  ← 파이프라인이 매일 자동 저장
  2. tracker.py 가 익일/5일 종가를 조회해 수익률 계산
  3. data/tracker/performance.json  ← 프론트엔드 /api/performance 엔드포인트가 서빙
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent.parent / "results"
TRACKER_DIR = Path(__file__).parent
PERFORMANCE_PATH = TRACKER_DIR / "performance.json"


# ── 1. 스크리닝 결과 추출 ──────────────────────────────────────────────────

def _find_final_node(results: dict) -> list[dict]:
    """tier와 total_score를 모두 가진 마지막 노드 데이터를 반환합니다."""
    candidates = [
        nr for nr in results.values()
        if "tier" in nr.get("columns", []) and "total_score" in nr.get("columns", [])
    ]
    if not candidates:
        return []
    # 종목 수가 가장 많은 노드를 최종 결과로 사용
    best = max(candidates, key=lambda x: x.get("total_count", 0))
    return best.get("data", [])


def extract_snapshot(filepath: Path) -> list[dict]:
    """screening JSON 파일 하나에서 타율 추적용 스냅샷을 추출합니다."""
    try:
        raw = json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("스냅샷 파일 읽기 실패 (%s): %s", filepath.name, e)
        return []

    as_of_date = raw.get("as_of_date", "")
    rows = _find_final_node(raw.get("results", {}))
    if not rows:
        return []

    snapshot = []
    for row in rows:
        tier = row.get("tier")
        if tier not in (1, 2, 3):
            continue
        snapshot.append({
            "as_of_date":   as_of_date,
            "code":         row.get("code", ""),
            "name":         row.get("name", ""),
            "tier":         tier,
            "total_score":  row.get("total_score"),
            "score_max":    row.get("score_max", 210),
            "entry_close":  row.get("close"),
            "rs_rating":    row.get("rs_rating"),
            "has_flow":     row.get("has_flow"),
            # 수익률은 나중에 채움
            "d1_close":     None,
            "d1_return":    None,
            "d5_close":     None,
            "d5_return":    None,
        })
    return snapshot


def load_all_snapshots() -> list[dict]:
    """results/ 폴더의 모든 스크리닝 파일에서 스냅샷을 통합합니다.
    날짜 당 가장 최신 파일(타임스탬프 최대) 1개만 사용합니다."""
    files = sorted(RESULTS_DIR.glob("screening_*.json"))

    # 날짜별 최신 파일만 선택
    by_date: dict[str, Path] = {}
    for f in files:
        parts = f.stem.split("_")         # ['screening', 'YYYY-MM-DD', 'HHMMSS']
        if len(parts) < 2:
            continue
        date_str = parts[1]
        if date_str not in by_date or f.name > by_date[date_str].name:
            by_date[date_str] = f

    snapshots = []
    for date_str, filepath in sorted(by_date.items()):
        rows = extract_snapshot(filepath)
        if rows:
            snapshots.extend(rows)
            logger.debug("스냅샷 추출: %s → %d종목", date_str, len(rows))

    return snapshots


# ── 2. 수익률 조회 ─────────────────────────────────────────────────────────

def _next_trading_close(krx_client, code: str, as_of_date: str, n: int) -> float | None:
    """as_of_date 기준 n번째 다음 영업일 종가를 반환합니다."""
    try:
        from data.holidays import prev_trading_day
        # n 영업일 후 날짜 추정 (달력일 * 1.5 여유)
        target_dt = pd.to_datetime(as_of_date) + pd.Timedelta(days=int(n * 1.8) + 3)
        target_str = target_dt.strftime("%Y-%m-%d")
        hist = krx_client.get_ohlcv(code, end_date=target_str, pages=2)
        if hist is None or hist.empty:
            return None
        # as_of_date 이후 n번째 영업일 종가
        future = hist[hist.index > pd.to_datetime(as_of_date)]
        if len(future) < n:
            return None
        return float(future["close"].iloc[n - 1])
    except Exception as e:
        logger.debug("종가 조회 실패 (code=%s, n=%d): %s", code, n, e)
        return None


def fill_returns(snapshots: list[dict], krx_client) -> list[dict]:
    """스냅샷에 익일(d1) 및 5일(d5) 수익률을 채웁니다.
    이미 수익률이 있는 항목은 건너뜁니다."""
    today = datetime.now().strftime("%Y-%m-%d")
    updated = 0

    for rec in snapshots:
        as_of = rec.get("as_of_date", "")
        entry = rec.get("entry_close")
        if not as_of or not entry:
            continue

        # d1: 이미 채워졌거나 익일 데이터가 아직 없으면 건너뜀
        if rec["d1_return"] is None and as_of < today:
            d1 = _next_trading_close(krx_client, rec["code"], as_of, n=1)
            if d1 and entry > 0:
                rec["d1_close"]  = d1
                rec["d1_return"] = round(d1 / entry - 1, 4)
                updated += 1

        # d5: 5 영업일 후 (주말 2일 포함 약 8 달력일 후부터 가능)
        if rec["d5_return"] is None:
            from data.holidays import prev_trading_day
            # as_of_date 기준 5영업일 후가 오늘보다 이르면 조회
            target_dt = pd.to_datetime(as_of) + pd.Timedelta(days=8)
            if target_dt.strftime("%Y-%m-%d") <= today:
                d5 = _next_trading_close(krx_client, rec["code"], as_of, n=5)
                if d5 and entry > 0:
                    rec["d5_close"]  = d5
                    rec["d5_return"] = round(d5 / entry - 1, 4)
                    updated += 1

    logger.info("수익률 업데이트: %d건", updated)
    return snapshots


# ── 3. 집계 ───────────────────────────────────────────────────────────────

def _tier_summary(records: list[dict], n_days: str) -> dict:
    """Tier별 타율/평균수익률 집계."""
    ret_key = f"{n_days}_return"
    summary = {}
    for tier in (1, 2, 3):
        tier_recs = [r for r in records if r.get("tier") == tier]
        with_return = [r for r in tier_recs if r.get(ret_key) is not None]
        if not with_return:
            summary[f"tier{tier}"] = {
                "count": len(tier_recs), "measured": 0,
                "hit_rate": None, "avg_return": None,
            }
            continue
        positive = sum(1 for r in with_return if r[ret_key] > 0)
        avg_ret  = sum(r[ret_key] for r in with_return) / len(with_return)
        summary[f"tier{tier}"] = {
            "count":      len(tier_recs),
            "measured":   len(with_return),
            "hit_rate":   round(positive / len(with_return), 3),
            "avg_return": round(avg_ret, 4),
        }
    return summary


def build_performance(snapshots: list[dict]) -> dict:
    """타율 집계 딕셔너리를 생성합니다."""
    return {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_records": len(snapshots),
        "d1_summary": _tier_summary(snapshots, "d1"),
        "d5_summary": _tier_summary(snapshots, "d5"),
        "records": snapshots,
    }


# ── 4. 저장/로드 ──────────────────────────────────────────────────────────

def save_performance(perf: dict) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    PERFORMANCE_PATH.write_text(
        json.dumps(perf, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("performance.json 저장 완료 (%d건)", perf["total_records"])


def load_performance() -> dict:
    if PERFORMANCE_PATH.exists():
        try:
            return json.loads(PERFORMANCE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"updated_at": None, "total_records": 0, "d1_summary": {}, "d5_summary": {}, "records": []}


# ── 5. 메인 업데이트 진입점 ───────────────────────────────────────────────

def run_tracker_update(krx_client) -> dict:
    """스냅샷 추출 → 수익률 조회 → 집계 → 저장까지 한 번에 실행합니다.
    scheduler/jobs.py 또는 API 엔드포인트에서 호출합니다."""
    logger.info("[Tracker] 타율 업데이트 시작")
    snapshots = load_all_snapshots()
    if not snapshots:
        logger.warning("[Tracker] 스냅샷 없음 — 스크리닝 결과 파일을 확인하세요")
        return load_performance()

    # 기존 performance.json에 수익률이 이미 채워진 것은 재사용
    existing = load_performance()
    existing_map = {
        (r["as_of_date"], r["code"]): r
        for r in existing.get("records", [])
    }
    for rec in snapshots:
        key = (rec["as_of_date"], rec["code"])
        if key in existing_map:
            prev = existing_map[key]
            if rec["d1_return"] is None and prev.get("d1_return") is not None:
                rec["d1_return"] = prev["d1_return"]
                rec["d1_close"]  = prev.get("d1_close")
            if rec["d5_return"] is None and prev.get("d5_return") is not None:
                rec["d5_return"] = prev["d5_return"]
                rec["d5_close"]  = prev.get("d5_close")

    snapshots = fill_returns(snapshots, krx_client)
    perf = build_performance(snapshots)
    save_performance(perf)
    logger.info("[Tracker] 완료 — Tier1 d1 타율: %s",
                perf["d1_summary"].get("tier1", {}).get("hit_rate"))
    return perf
