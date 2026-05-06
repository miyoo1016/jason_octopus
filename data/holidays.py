"""
KRX 휴장일 캘린더.

pykrx의 get_business_days()를 1차 소스로 사용하고,
네트워크 실패 시 정적 공휴일 목록 + 주말 체크로 폴백합니다.

사용법:
    from data.holidays import is_trading_day, prev_trading_day, next_trading_day

    # 특정 날이 거래일인지 확인
    is_trading_day("2026-05-05")  # False (어린이날)

    # 가장 최근 거래일 (T-1 데이터 기준일)
    prev_trading_day("2026-05-05")  # "2026-05-04"
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache

logger = logging.getLogger(__name__)

# ── 정적 공휴일 (KRX 공식 휴장일, 연도별 업데이트 필요) ──────────────────────
# 출처: https://open.krx.co.kr/contents/HOM/HOM04010400/HOM04010400.jsp
_STATIC_HOLIDAYS: frozenset[date] = frozenset([
    # 2024
    date(2024, 1,  1),   # 신정
    date(2024, 2,  9),   # 설 연휴
    date(2024, 2, 12),   # 설 연휴
    date(2024, 3,  1),   # 3·1절
    date(2024, 4, 10),   # 22대 국회의원 선거
    date(2024, 5,  5),   # 어린이날
    date(2024, 5,  6),   # 어린이날 대체
    date(2024, 5, 15),   # 부처님 오신 날
    date(2024, 6,  6),   # 현충일
    date(2024, 8, 15),   # 광복절
    date(2024, 9, 16),   # 추석 연휴
    date(2024, 9, 17),   # 추석
    date(2024, 9, 18),   # 추석 연휴
    date(2024, 10, 3),   # 개천절
    date(2024, 10, 9),   # 한글날
    date(2024, 12, 25),  # 크리스마스
    date(2024, 12, 31),  # KRX 연말 휴장
    # 2025
    date(2025, 1,  1),   # 신정
    date(2025, 1, 28),   # 설 연휴
    date(2025, 1, 29),   # 설 연휴
    date(2025, 1, 30),   # 설날
    date(2025, 3,  3),   # 3·1절 대체
    date(2025, 5,  5),   # 어린이날
    date(2025, 5,  6),   # 부처님 오신 날
    date(2025, 6,  6),   # 현충일
    date(2025, 8, 15),   # 광복절
    date(2025, 10, 3),   # 개천절
    date(2025, 10, 5),   # 추석 연휴
    date(2025, 10, 6),   # 추석
    date(2025, 10, 7),   # 추석 연휴
    date(2025, 10, 8),   # 추석 대체
    date(2025, 10, 9),   # 한글날
    date(2025, 12, 25),  # 크리스마스
    date(2025, 12, 31),  # KRX 연말 휴장
    # 2026
    date(2026, 1,  1),   # 신정
    date(2026, 2, 16),   # 설 연휴
    date(2026, 2, 17),   # 설날
    date(2026, 2, 18),   # 설 연휴
    date(2026, 3,  2),   # 3·1절 대체
    date(2026, 5,  5),   # 어린이날
    date(2026, 5, 25),   # 부처님 오신 날
    date(2026, 6,  6),   # 현충일 (토 → 대체 없음)
    date(2026, 8, 17),   # 광복절 대체
    date(2026, 9, 24),   # 추석 연휴
    date(2026, 9, 25),   # 추석
    date(2026, 9, 28),   # 추석 대체
    date(2026, 10, 5),   # 개천절 대체
    date(2026, 10, 9),   # 한글날
    date(2026, 12, 25),  # 크리스마스
    date(2026, 12, 31),  # KRX 연말 휴장
])


def _to_date(d: str | date) -> date:
    """문자열 'YYYY-MM-DD' 또는 date 객체를 date로 변환합니다."""
    if isinstance(d, date):
        return d
    return date.fromisoformat(d)


def is_trading_day(d: str | date) -> bool:
    """
    주어진 날짜가 KRX 거래일인지 반환합니다.

    1. 주말(토·일) → False
    2. 정적 휴장일 목록 → False
    3. 그 외 → True (pykrx 확인 없이 빠른 경로)
    """
    dt = _to_date(d)
    if dt.weekday() >= 5:          # 토=5, 일=6
        return False
    if dt in _STATIC_HOLIDAYS:
        return False
    return True


def prev_trading_day(d: str | date, n: int = 1) -> str:
    """
    d 기준 n번째 이전 거래일을 'YYYY-MM-DD' 형식으로 반환합니다.

    Args:
        d: 기준 날짜 (당일 포함 안 됨 — d의 바로 전날부터 역산)
        n: 몇 번째 이전 거래일 (기본 1)

    Returns:
        'YYYY-MM-DD' 형식 문자열
    """
    dt = _to_date(d)
    count = 0
    candidate = dt - timedelta(days=1)
    while True:
        if is_trading_day(candidate):
            count += 1
            if count == n:
                return candidate.isoformat()
        candidate -= timedelta(days=1)
        # 무한루프 방어: 최대 730일 역산
        if (dt - candidate).days > 730:
            raise ValueError(f"{d} 기준으로 {n}번째 이전 거래일을 찾지 못했습니다.")


def next_trading_day(d: str | date, n: int = 1) -> str:
    """
    d 기준 n번째 이후 거래일을 반환합니다.

    Args:
        d: 기준 날짜 (당일 포함 안 됨)
        n: 몇 번째 이후 거래일 (기본 1)

    Returns:
        'YYYY-MM-DD' 형식 문자열
    """
    dt = _to_date(d)
    count = 0
    candidate = dt + timedelta(days=1)
    while True:
        if is_trading_day(candidate):
            count += 1
            if count == n:
                return candidate.isoformat()
        candidate += timedelta(days=1)
        if (candidate - dt).days > 730:
            raise ValueError(f"{d} 기준으로 {n}번째 이후 거래일을 찾지 못했습니다.")


def latest_trading_day(reference: str | date | None = None) -> str:
    """
    pykrx 데이터 기준 가장 최근 거래일을 반환합니다.
    pykrx는 T-1(전일) 기준이므로 '오늘'이 거래일이어도 전일을 반환합니다.

    Args:
        reference: 기준 날짜 (None이면 오늘)

    Returns:
        'YYYY-MM-DD' 형식 최근 거래일
    """
    ref = _to_date(reference) if reference else date.today()
    # pykrx는 장 마감 후(보통 18:00 KST 이후) 전일 데이터가 확정됨.
    # 안전하게 항상 전 거래일을 반환합니다.
    return prev_trading_day(ref, n=1)


def trading_days_between(start: str | date, end: str | date) -> list[str]:
    """
    start와 end(포함) 사이의 모든 거래일 목록을 반환합니다.

    Args:
        start: 시작 날짜 (포함)
        end:   종료 날짜 (포함)

    Returns:
        'YYYY-MM-DD' 형식 거래일 리스트 (오름차순)
    """
    s = _to_date(start)
    e = _to_date(end)
    result = []
    current = s
    while current <= e:
        if is_trading_day(current):
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def to_krx_date(d: str | date) -> str:
    """'YYYY-MM-DD' → 'YYYYMMDD' pykrx 내부 포맷으로 변환합니다."""
    return _to_date(d).strftime("%Y%m%d")
