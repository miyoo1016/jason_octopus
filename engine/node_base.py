"""
DAG 노드 기본 클래스와 실행 컨텍스트.

모든 노드는 BaseNode를 상속하고 다음 클래스 변수와 run() 메서드를 정의해야 합니다:

    NODE_TYPE              유일 식별자 (예: "vcp", "and_filter")
    DISPLAY_NAME           한국어 표시명 (예: "VCP 패턴 찾기")
    INPUT_ARITY            입력 DataFrame 개수
                              0 = 소스 노드 (universe 등, 외부에서 데이터 수집)
                              1 = 변환 노드 (필터, 점수 등)
                              2 = 결합 노드 (AND, OR 등)
    REQUIRED_INPUT_COLUMNS 입력에 반드시 있어야 할 컬럼들
                            (표준 컬럼 code/name/market/close/volume은 항상 제공됨 가정)
    OUTPUT_COLUMNS         이 노드가 출력에 추가/유지하는 컬럼들
    ParamsModel            pydantic 파라미터 모델

run()의 입력/출력은 항상 pandas DataFrame입니다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar

import pandas as pd
from pydantic import BaseModel, ConfigDict


# ── 모든 노드가 공통으로 보유하는 표준 컬럼 ──────────────────────────────────
# 이 컬럼들은 어느 노드의 출력에서도 항상 존재한다고 가정합니다.
STANDARD_COLUMNS: frozenset[str] = frozenset({
    "code", "name", "market", "close", "volume",
})


@dataclass
class ExecutionContext:
    """
    노드 실행 시 전체 DAG가 공유하는 컨텍스트.

    Attributes:
        as_of_date:   기준일 'YYYY-MM-DD' (모든 데이터 조회의 기준점)
        run_id:       실행 고유 ID (로깅·디버깅용)
        cache_dir:    결과 캐시 저장 경로 (노드가 임시 파일을 쓸 때 참조)
    krx_client:   KRX 데이터 클라이언트 (소스 노드만 사용, 그 외 None 허용)
    extras:       특수 노드를 위한 추가 자원 (LLM 클라이언트 등)
    """
    as_of_date: str
    run_id: str
    cache_dir: str = ""
    krx_client: Any = None
    is_single_analysis: bool = False  # 특정 종목 정밀 분석 모드 (필터링 방지)
    extras: dict[str, Any] = field(default_factory=dict)
    progress_callback: Callable[[dict[str, Any]], None] | None = None


class EmptyParams(BaseModel):
    """파라미터가 없는 노드를 위한 기본 모델."""
    model_config = ConfigDict(extra="forbid")


class BaseNode(ABC):
    """
    모든 DAG 노드의 추상 부모 클래스.

    하위 클래스는 클래스 변수(NODE_TYPE, DISPLAY_NAME, INPUT_ARITY,
    REQUIRED_INPUT_COLUMNS, OUTPUT_COLUMNS, ParamsModel)와 run()을 구현합니다.
    """

    # ── 하위 클래스가 반드시 정의해야 할 클래스 변수들 ────────────────────
    NODE_TYPE:              ClassVar[str]
    DISPLAY_NAME:           ClassVar[str]
    DESCRIPTION:            ClassVar[str] = ""
    INPUT_ARITY:            ClassVar[int]
    REQUIRED_INPUT_COLUMNS: ClassVar[tuple[str, ...]] = ()
    OUTPUT_COLUMNS:         ClassVar[tuple[str, ...]] = ()
    ParamsModel:            ClassVar[type[BaseModel]] = EmptyParams

    # ── 검증 ─────────────────────────────────────────────────────────────
    @classmethod
    def validate_params(cls, params_dict: dict[str, Any] | None) -> BaseModel:
        """params 딕셔너리를 ParamsModel로 검증·변환합니다."""
        return cls.ParamsModel(**(params_dict or {}))

    @classmethod
    def output_columns_set(cls) -> frozenset[str]:
        """이 노드의 출력 컬럼 (표준 컬럼 포함)."""
        return frozenset(cls.OUTPUT_COLUMNS) | STANDARD_COLUMNS

    @classmethod
    def required_columns_set(cls) -> frozenset[str]:
        """이 노드의 필수 입력 컬럼 (표준 컬럼 제외하고 명시된 것만)."""
        return frozenset(cls.REQUIRED_INPUT_COLUMNS)

    # ── 실행 본체 ────────────────────────────────────────────────────────
    @abstractmethod
    def run(
        self,
        inputs: list[pd.DataFrame],
        params: BaseModel,
        context: ExecutionContext,
    ) -> pd.DataFrame:
        """
        노드 실행 본체.

        Args:
            inputs:  입력 DataFrame 리스트 (길이 = INPUT_ARITY, 슬롯 순서대로)
            params:  검증된 파라미터 (ParamsModel 인스턴스)
            context: 실행 컨텍스트

        Returns:
            출력 DataFrame. OUTPUT_COLUMNS의 컬럼을 모두 포함해야 합니다.
        """

    # ── 메타데이터 ───────────────────────────────────────────────────────
    @classmethod
    def info(cls) -> dict[str, Any]:
        """UI/디버깅용 노드 메타데이터."""
        return {
            "node_type":              cls.NODE_TYPE,
            "display_name":           cls.DISPLAY_NAME,
            "description":            cls.DESCRIPTION,
            "input_arity":            cls.INPUT_ARITY,
            "required_input_columns": list(cls.REQUIRED_INPUT_COLUMNS),
            "output_columns":         list(cls.OUTPUT_COLUMNS),
            "params_schema":          cls.ParamsModel.model_json_schema(),
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} type={self.NODE_TYPE!r}>"
