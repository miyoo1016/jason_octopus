"""
DAG 실행 엔진 — 시스템의 심장.

핵심 책임:
  1. 노드 등록 / 엣지 연결 (build)
  2. 검증 (validate): 사이클·고립·입력 슬롯 누락·컬럼 호환성
  3. 위상 정렬 (Kahn 알고리즘)
  4. 캐시 키 생성 (SHA256: node_type + params + as_of_date + parent_keys)
  5. 부분 재실행: params 변경 시 영향 받는 하위 노드만 재실행
  6. 실패 격리: 한 노드 실패 시 하위 노드는 'skipped'로 처리, 다른 분기는 계속 실행
  7. 실행 로그 수집

캐시 키 결정성 보장:
  - params는 model_dump(mode="json") 후 sort_keys=True로 직렬화
  - parent keys는 sorted()로 정렬 후 join → 순서 무관
  - 동일 입력 → 동일 키 → 캐시 히트
  - params 1개만 바꿔도 그 노드 + 하위 모든 노드의 키가 바뀜
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ValidationError

from engine.cache import ResultCache
from engine.node_base import STANDARD_COLUMNS, BaseNode, ExecutionContext

logger = logging.getLogger(__name__)


# ── 예외 ─────────────────────────────────────────────────────────────────────

class DAGValidationError(ValueError):
    """DAG 구조 또는 파라미터 검증 실패 시 발생합니다."""


class LeakageError(RuntimeError):
    """
    Look-ahead bias(미래 데이터 누출) 감지 시 발생합니다.

    노드 또는 백테스트 엔진이 as_of_date 이후의 데이터를 참조하려 할 때
    이 예외가 발생합니다. 백테스트 결과의 신뢰성을 보장하는 마지막 방어선입니다.
    """


# ── 데이터 클래스 ────────────────────────────────────────────────────────────

@dataclass
class NodeInstance:
    """DAG 안에 등록된 노드 한 개."""
    node_id: str
    node:    BaseNode
    params:  BaseModel

    @property
    def node_type(self) -> str:
        return self.node.NODE_TYPE


@dataclass(frozen=True)
class Edge:
    """DAG 엣지: source → target.input_slot."""
    source:     str
    target:     str
    input_slot: int = 0


NodeStatus = Literal["ok", "error", "skipped", "cache_hit"]


@dataclass
class NodeExecutionLog:
    node_id:      str
    node_type:    str
    display_name: str
    status:       NodeStatus
    input_count:  int   = 0
    output_count: int   = 0
    latency_ms:   float = 0.0
    cache_hit:    bool  = False
    cache_key:    str   = ""
    error:        str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id":      self.node_id,
            "node_type":    self.node_type,
            "display_name": self.display_name,
            "status":       self.status,
            "input_count":  self.input_count,
            "output_count": self.output_count,
            "latency_ms":   round(self.latency_ms, 1),
            "cache_hit":    self.cache_hit,
            "cache_key":    self.cache_key[:16] + "…" if self.cache_key else "",
            "error":        self.error,
        }


@dataclass
class ExecutionResult:
    run_id:       str
    dag_name:     str
    as_of_date:   str
    started_at:   datetime
    finished_at:  datetime
    node_logs:    list[NodeExecutionLog]
    outputs:      dict[str, pd.DataFrame]   # node_id → 결과 DataFrame
    success:      bool

    @property
    def total_latency_ms(self) -> float:
        return sum(log.latency_ms for log in self.node_logs)

    @property
    def cache_hit_rate(self) -> float:
        if not self.node_logs:
            return 0.0
        hits = sum(1 for log in self.node_logs if log.cache_hit)
        return hits / len(self.node_logs)

    def leaf_outputs(self, dag: "DAG") -> dict[str, pd.DataFrame]:
        """리프 노드(자식 없는 노드)의 출력만 추출."""
        return {nid: self.outputs[nid] for nid in dag.find_leaves() if nid in self.outputs}

    def summary(self) -> dict[str, Any]:
        return {
            "run_id":            self.run_id,
            "dag_name":          self.dag_name,
            "as_of_date":        self.as_of_date,
            "started_at":        self.started_at.isoformat(),
            "finished_at":       self.finished_at.isoformat(),
            "success":           self.success,
            "total_latency_ms":  round(self.total_latency_ms, 1),
            "cache_hit_rate":    round(self.cache_hit_rate, 3),
            "node_logs":         [log.as_dict() for log in self.node_logs],
        }


# ── DAG 본체 ─────────────────────────────────────────────────────────────────

class DAG:
    """노드와 엣지로 구성된 실행 가능한 DAG."""

    def __init__(self, name: str = "unnamed") -> None:
        self.name = name
        self._nodes: dict[str, NodeInstance] = {}
        self._edges: list[Edge] = []

    # ── 빌드 API ─────────────────────────────────────────────────────────

    def add_node(
        self,
        node_id: str,
        node:    BaseNode,
        params:  dict[str, Any] | None = None,
    ) -> "DAG":
        """노드를 DAG에 추가합니다.

        Args:
            node_id: DAG 내에서 유일한 식별자 (사용자 지정)
            node:    BaseNode 인스턴스
            params:  파라미터 딕셔너리 (ParamsModel로 검증됨)

        Returns:
            self (체이닝 지원)

        Raises:
            DAGValidationError: node_id 중복 또는 params 검증 실패
        """
        if node_id in self._nodes:
            raise DAGValidationError(f"노드 ID 중복: {node_id!r}")
        try:
            validated = node.validate_params(params)
        except ValidationError as exc:
            raise DAGValidationError(
                f"노드 {node_id!r}({node.NODE_TYPE}) 파라미터 검증 실패: {exc}"
            ) from exc
        self._nodes[node_id] = NodeInstance(node_id=node_id, node=node, params=validated)
        return self

    def add_edge(self, source: str, target: str, input_slot: int = -1) -> "DAG":
        """엣지 추가. source의 출력이 target의 input_slot으로 들어갑니다.
        input_slot=-1이면 자동으로 다음 빈 슬롯을 할당합니다.

        Raises:
            DAGValidationError: source/target 미존재, 자기 참조
        """
        if source not in self._nodes:
            raise DAGValidationError(f"존재하지 않는 source: {source!r}")
        if target not in self._nodes:
            raise DAGValidationError(f"존재하지 않는 target: {target!r}")
        if source == target:
            raise DAGValidationError(f"자기 참조 엣지 금지: {source!r} → {target!r}")

        # 중복 엣지 방지 (같은 source→target 쌍)
        for e in self._edges:
            if e.source == source and e.target == target:
                return self

        used_slots = {e.input_slot for e in self._edges if e.target == target}

        if input_slot < 0:
            # 자동 슬롯 할당: 다음 빈 슬롯
            input_slot = 0
            while input_slot in used_slots:
                input_slot += 1
        elif input_slot in used_slots:
            # 명시적 슬롯이 이미 점유된 경우 에러
            raise DAGValidationError(
                f"슬롯 이미 연결됨: {target!r}.input_slot={input_slot}"
            )

        self._edges.append(Edge(source=source, target=target, input_slot=input_slot))
        return self

    # ── 검증 ─────────────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """DAG 구조를 검증하고 에러 목록을 반환합니다 (빈 리스트 = 정상)."""
        errors: list[str] = []

        # 1. 사이클 탐지
        if self._has_cycle():
            errors.append("사이클이 존재합니다.")
            return errors  # 사이클이면 이후 검증 의미 없음

        # 2. 입력 슬롯 충족도
        for node_id, inst in self._nodes.items():
            arity = inst.node.INPUT_ARITY
            connected_count = len([
                e for e in self._edges if e.target == node_id
            ])
            if arity == 0 and connected_count > 0:
                errors.append(
                    f"{node_id!r}: 소스 노드에 입력 연결 불가"
                )
            elif arity > 0 and connected_count < arity:
                errors.append(
                    f"{node_id!r}: 입력 간선 필요, {arity}개 필요하나 {connected_count}개만 연결됨"
                )

        # 3. 컬럼 호환성: target의 REQUIRED_INPUT_COLUMNS ⊆ source의 OUTPUT_COLUMNS
        for edge in self._edges:
            src = self._nodes[edge.source]
            tgt = self._nodes[edge.target]
            required = tgt.node.required_columns_set()
            provided = src.node.output_columns_set()   # 표준 컬럼 포함
            missing  = required - provided
            if missing:
                errors.append(
                    f"엣지 {edge.source!r}→{edge.target!r}: "
                    f"필수 컬럼 부족 {sorted(missing)}"
                )

        return errors

    # ── 분석 헬퍼 ────────────────────────────────────────────────────────

    def parents(self, node_id: str) -> list[Edge]:
        """node_id의 입력 엣지들을 슬롯 순으로 반환."""
        return sorted(
            (e for e in self._edges if e.target == node_id),
            key=lambda e: e.input_slot,
        )

    def children(self, node_id: str) -> list[str]:
        """node_id의 자식 노드 ID 목록."""
        return [e.target for e in self._edges if e.source == node_id]

    def find_leaves(self) -> list[str]:
        """리프 노드 (자식 없는 노드) 목록."""
        has_child = {e.source for e in self._edges}
        return [n for n in self._nodes if n not in has_child]

    def find_roots(self) -> list[str]:
        """루트 노드 (부모 없는 노드) 목록."""
        has_parent = {e.target for e in self._edges}
        return [n for n in self._nodes if n not in has_parent]

    def _has_cycle(self) -> bool:
        """DFS 기반 사이클 탐지."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._nodes}
        adj:   dict[str, list[str]] = defaultdict(list)
        for e in self._edges:
            adj[e.source].append(e.target)

        def dfs(u: str) -> bool:
            color[u] = GRAY
            for v in adj[u]:
                if color[v] == GRAY:
                    return True
                if color[v] == WHITE and dfs(v):
                    return True
            color[u] = BLACK
            return False

        for n in self._nodes:
            if color[n] == WHITE and dfs(n):
                return True
        return False

    def topological_sort(self) -> list[str]:
        """Kahn 알고리즘으로 위상 정렬된 node_id 리스트 반환."""
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for e in self._edges:
            in_degree[e.target] += 1

        # 결정성을 위해 ID 알파벳 순으로 큐 초기화
        queue = deque(sorted(n for n, d in in_degree.items() if d == 0))
        order: list[str] = []
        adj: dict[str, list[str]] = defaultdict(list)
        for e in self._edges:
            adj[e.source].append(e.target)

        while queue:
            u = queue.popleft()
            order.append(u)
            # 자식 노드들도 결정적 순서로 처리
            for v in sorted(adj[u]):
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)

        if len(order) != len(self._nodes):
            raise DAGValidationError("위상 정렬 실패 (사이클 존재)")
        return order

    # ── 캐시 키 ──────────────────────────────────────────────────────────

    def compute_cache_key(self, node_id: str, as_of_date: str) -> str:
        """단일 노드의 캐시 키를 계산합니다 (외부 사용용)."""
        return self._compute_cache_key(node_id, as_of_date, memo={})

    def _compute_cache_key(
        self,
        node_id: str,
        as_of_date: str,
        memo: dict[str, str],
    ) -> str:
        """
        재귀적 캐시 키 계산.

        키 구조:
            sha256(node_type | params_json | as_of_date | upstream_signature)

        upstream_signature:
            sha256("|".join(sorted(parent_keys)))[:16]

        결정성 보장:
            - params는 sort_keys=True로 직렬화
            - parent_keys는 정렬 후 join → 순서 무관
            - 동일 입력 → 동일 키 (재실행 시에도)
        """
        if node_id in memo:
            return memo[node_id]

        inst = self._nodes[node_id]
        params_json = json.dumps(
            inst.params.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )

        # 부모들의 캐시 키를 정렬 → 결합 → 해싱 (다중 입력 시 순서 무관)
        parent_ids = sorted(e.source for e in self._edges if e.target == node_id)
        parent_keys = [self._compute_cache_key(p, as_of_date, memo) for p in parent_ids]
        upstream_sig = hashlib.sha256(
            "|".join(parent_keys).encode("utf-8")
        ).hexdigest()[:16] if parent_keys else "ROOT"

        signature = f"{inst.node_type}|{params_json}|{as_of_date}|{upstream_sig}"
        key = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        memo[node_id] = key
        return key

    # ── 실행 ─────────────────────────────────────────────────────────────

    def execute(
        self,
        as_of_date:    str,
        result_cache:  ResultCache,
        extras:        dict[str, Any] | None = None,
        force_refresh: bool = False,
        is_single:     bool = False,
    ) -> ExecutionResult:
        """
        DAG를 위상 순서대로 실행합니다.

        Args:
            as_of_date:    기준일 'YYYY-MM-DD'
            result_cache:  ResultCache 인스턴스
            krx_client:    소스 노드용 KRX 클라이언트 (선택)
            extras:        특수 노드용 추가 자원 (LLM 클라이언트 등)
            force_refresh: True이면 캐시 무시하고 모든 노드 재실행

        Returns:
            ExecutionResult — 실행 로그·출력·성공 여부
        """
        errors = self.validate()
        if errors:
            raise DAGValidationError("DAG 검증 실패: " + "; ".join(errors))

        run_id = "run_" + uuid.uuid4().hex[:12]
        started_at = datetime.now()
        order = self.topological_sort()

        cache_keys: dict[str, str] = {}
        for nid in order:
            cache_keys[nid] = self._compute_cache_key(nid, as_of_date, memo=cache_keys)

        outputs:      dict[str, pd.DataFrame] = {}
        logs:         list[NodeExecutionLog]  = []
        failed_nodes: set[str] = set()

        ctx = ExecutionContext(
            as_of_date=as_of_date,
            run_id=run_id,
            cache_dir=str(result_cache.cache_dir),
            krx_client=krx_client,
            is_single_analysis=is_single,
            extras=extras or {},
        )

        for node_id in order:
            inst = self._nodes[node_id]
            cache_key = cache_keys[node_id]
            logger.info(">>> [%s] 노드 실행 시작 (%s)", node_id, inst.node_type)

            log = NodeExecutionLog(
                node_id=node_id,
                node_type=inst.node_type,
                display_name=inst.node.DISPLAY_NAME,
                status="ok",
                cache_key=cache_key,
            )

            # 부모 노드 중 실패한 게 있으면 스킵 (실패 격리)
            parent_ids = [e.source for e in self.parents(node_id)]
            if any(p in failed_nodes for p in parent_ids):
                log.status = "skipped"
                log.error  = "상위 노드 실패로 스킵됨"
                logs.append(log)
                failed_nodes.add(node_id)
                logger.info("노드 스킵: %s (상위 실패)", node_id)
                continue

            # 캐시 히트? (기준일이 오늘이면 실시간 데이터 반영을 위해 캐시 무시)
            is_today = (as_of_date == datetime.now().strftime("%Y-%m-%d"))
            if not force_refresh and not is_today:
                cached = result_cache.get(cache_key)
                if cached is not None:
                    log.status       = "cache_hit"
                    log.cache_hit    = True
                    log.output_count = len(cached)
                    outputs[node_id] = cached
                    logs.append(log)
                    logger.info("캐시 히트: %s (%d행)", node_id, len(cached))
                    continue
            elif is_today:
                logger.info("오늘 날짜 분석: 실시간 반영을 위해 캐시 우회 (%s)", node_id)

            # 입력 수집 (슬롯 순서대로)
            inputs: list[pd.DataFrame] = []
            try:
                for edge in self.parents(node_id):
                    src_output = outputs[edge.source]
                    inputs.append(src_output)
            except KeyError as exc:
                # 이론상 도달 불가 (위상 정렬 보장)
                log.status = "error"
                log.error  = f"입력 수집 실패: {exc}"
                logs.append(log)
                failed_nodes.add(node_id)
                continue

            log.input_count = sum(len(i) for i in inputs)

            # 실제 실행
            t0 = time.perf_counter()
            try:
                result = inst.node.run(inputs, inst.params, ctx)
                if not isinstance(result, pd.DataFrame):
                    raise TypeError(
                        f"노드 {node_id}가 DataFrame이 아닌 {type(result).__name__}을 반환함"
                    )
                log.latency_ms   = (time.perf_counter() - t0) * 1000
                log.output_count = len(result)
                outputs[node_id] = result
                result_cache.put(cache_key, result)
                logger.info(
                    "노드 완료: %s | %d행 | %.0fms",
                    node_id, len(result), log.latency_ms,
                )
            except Exception as exc:
                log.latency_ms = (time.perf_counter() - t0) * 1000
                log.status     = "error"
                log.error      = f"{type(exc).__name__}: {exc}"
                failed_nodes.add(node_id)
                logger.exception("노드 실행 실패: %s", node_id)

            logs.append(log)

        finished_at = datetime.now()
        return ExecutionResult(
            run_id=run_id,
            dag_name=self.name,
            as_of_date=as_of_date,
            started_at=started_at,
            finished_at=finished_at,
            node_logs=logs,
            outputs=outputs,
            success=len(failed_nodes) == 0,
        )

    # ── 직렬화 (Antigravity 호환) ────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화 가능한 딕셔너리. UI/저장용."""
        return {
            "name": self.name,
            "nodes": [
                {
                    "id":      inst.node_id,
                    "type":    inst.node_type,
                    "display": inst.node.DISPLAY_NAME,
                    "params":  inst.params.model_dump(mode="json"),
                }
                for inst in self._nodes.values()
            ],
            "edges": [
                {"source": e.source, "target": e.target, "slot": e.input_slot}
                for e in self._edges
            ],
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        node_registry: dict[str, type[BaseNode]],
    ) -> "DAG":
        """딕셔너리에서 DAG 복원.

        Args:
            data:          to_dict()로 생성된 딕셔너리
            node_registry: {node_type: NodeClass} 매핑 (사용자 제공)
        """
        dag = cls(name=data.get("name", "unnamed"))
        for n in data.get("nodes", []):
            node_type = n["type"]
            if node_type not in node_registry:
                raise DAGValidationError(f"알 수 없는 노드 타입: {node_type}")
            node_cls = node_registry[node_type]
            dag.add_node(n["id"], node_cls(), n.get("params"))
        for e in data.get("edges", []):
            dag.add_edge(e["source"], e["target"], e.get("slot", 0))
        return dag

    # ── 인스펙션 ─────────────────────────────────────────────────────────

    @property
    def node_ids(self) -> list[str]:
        return list(self._nodes.keys())

    @property
    def edges(self) -> list[Edge]:
        return list(self._edges)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def get_node(self, node_id: str) -> NodeInstance:
        return self._nodes[node_id]

    def __repr__(self) -> str:
        return f"<DAG name={self.name!r} nodes={self.node_count} edges={self.edge_count}>"
