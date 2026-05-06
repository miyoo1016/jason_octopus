"""
Look-ahead bias 방어선.

백테스트 시 미래 데이터가 신호 생성에 누출되는 것을 차단합니다.
모든 데이터 수집 메서드와 노드는 이 모듈의 가드를 통과해야 합니다.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd

from engine.dag import LeakageError

logger = logging.getLogger(__name__)


def _to_date(d: str | date | datetime | pd.Timestamp) -> date:
    """다양한 날짜 표현을 date로 정규화."""
    if isinstance(d, str):
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    raise TypeError(f"지원하지 않는 날짜 타입: {type(d)}")


def assert_no_future_data(
    df: pd.DataFrame,
    as_of_date: str | date,
    *,
    date_col: str | None = None,
    context: str = "",
) -> None:
    """
    DataFrame 내 모든 날짜가 as_of_date 이하임을 검증합니다.

    Args:
        df:         검증 대상 DataFrame
        as_of_date: 허용 최대 날짜 (포함)
        date_col:   날짜 컬럼명. None이면 DatetimeIndex 사용
        context:    오류 메시지에 포함될 호출 위치 식별자

    Raises:
        LeakageError: as_of_date 이후 데이터가 1건이라도 존재할 경우
    """
    if df is None or df.empty:
        return

    boundary = _to_date(as_of_date)

    if date_col is None:
        # DatetimeIndex 가정
        if not isinstance(df.index, pd.DatetimeIndex):
            return  # 날짜 인덱스가 아니면 검증 대상 외
        max_date = df.index.max()
        if pd.isna(max_date):
            return
        max_d = _to_date(max_date)
    else:
        if date_col not in df.columns:
            return
        s = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if s.empty:
            return
        max_d = _to_date(s.max())

    if max_d > boundary:
        raise LeakageError(
            f"미래 데이터 누출 감지 ({context or 'unknown'}): "
            f"as_of_date={boundary.isoformat()}, "
            f"데이터 최대일={max_d.isoformat()}"
        )


def filter_to_as_of(
    df: pd.DataFrame,
    as_of_date: str | date,
    *,
    date_col: str | None = None,
) -> pd.DataFrame:
    """
    DataFrame을 as_of_date 이하 행만 남도록 필터링합니다.

    데이터 소스(API 응답)가 항상 최신 데이터를 포함하므로,
    백테스트 시 입력 단계에서 이 함수로 잘라야 합니다.
    """
    if df is None or df.empty:
        return df

    boundary = _to_date(as_of_date)

    if date_col is None:
        if not isinstance(df.index, pd.DatetimeIndex):
            return df
        mask = df.index.date <= boundary
        return df.loc[mask]
    else:
        if date_col not in df.columns:
            return df
        dates = pd.to_datetime(df[date_col], errors="coerce")
        mask = dates.dt.date <= boundary
        return df.loc[mask].copy()


def assert_after(
    df: pd.DataFrame,
    boundary_date: str | date,
    *,
    date_col: str | None = None,
    context: str = "",
) -> None:
    """
    DataFrame 내 모든 날짜가 boundary_date 초과임을 검증합니다.
    BacktestEngine에서 진입가 계산용 데이터가 신호일 이후인지 확인할 때 사용.

    Raises:
        LeakageError: boundary_date 이하 데이터가 포함된 경우
    """
    if df is None or df.empty:
        return

    boundary = _to_date(boundary_date)

    if date_col is None:
        if not isinstance(df.index, pd.DatetimeIndex):
            return
        min_date = df.index.min()
        if pd.isna(min_date):
            return
        min_d = _to_date(min_date)
    else:
        if date_col not in df.columns:
            return
        s = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if s.empty:
            return
        min_d = _to_date(s.min())

    if min_d <= boundary:
        raise LeakageError(
            f"신호일 데이터 누출 감지 ({context or 'unknown'}): "
            f"signal_date={boundary.isoformat()}, "
            f"진입 데이터 최소일={min_d.isoformat()} (signal_date 이하)"
        )
