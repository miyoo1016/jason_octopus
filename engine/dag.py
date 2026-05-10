from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, ValidationError

from engine.node_base import ExecutionContext

logger = logging.getLogger(__name__)


class DAGValidationError(Exception):
    """DAG 구조 또는 노드 계약이 유효하지 않을 때 발생합니다."""


class LeakageError(Exception):
    """미래 데이터 참조(Look-ahead Bias) 발생 시 발생하는 예외."""


class NodeExecutionLog:
    def __init__(
        self,
        node_id: str,
        node_type: str,
        display_name: str,
        status: str = "ok",
        cache_key: str = "",
    ):
        self.node_id = node_id
        self.node_type = node_type
        self.display_name = display_name
        self.status = status
        self.cache_key = cache_key
        self.input_count = 0
        self.output_count = 0
        self.dropped_count = 0
        self.latency_ms = 0.0
        self.cache_hit = False
        self.error: str | None = None
        self.drop_reasons: list[dict[str, Any]] = []
        self.data_missing_count = 0
        self.data_missing_ratio = 0.0
        self.nan_columns: list[dict[str, Any]] = []


class ExecutionResult:
    def __init__(self, *, as_of_date: str = ""):
        self.success = True
        self.run_id = ""
        self.as_of_date = as_of_date
        self.outputs: dict[str, pd.DataFrame] = {}
        self.node_logs: list[NodeExecutionLog] = []
        self.error: str | None = None
        self.total_latency_ms = 0.0

    @property
    def cache_hit_rate(self) -> float:
        """캐시 히트 비율 (0.0 ~ 1.0)."""
        if not self.node_logs:
            return 0.0
        hits = sum(1 for log in self.node_logs if log.cache_hit)
        return hits / len(self.node_logs)

    def summary(self) -> dict[str, Any]:
        """실행 결과 요약 (직렬화용)."""
        return {
            "success": self.success,
            "run_id": self.run_id,
            "as_of_date": self.as_of_date,
            "total_latency_ms": self.total_latency_ms,
            "cache_hit_rate": self.cache_hit_rate,
            "error": self.error,
            "node_logs": [
                {
                    "node_id": log.node_id,
                    "node_type": log.node_type,
                    "status": log.status,
                    "input_count": log.input_count,
                    "output_count": log.output_count,
                    "dropped_count": log.dropped_count,
                    "latency_ms": log.latency_ms,
                    "cache_hit": log.cache_hit,
                    "error": log.error,
                    "drop_reasons": log.drop_reasons,
                    "data_missing_count": log.data_missing_count,
                    "data_missing_ratio": log.data_missing_ratio,
                    "nan_columns": log.nan_columns,
                }
                for log in self.node_logs
            ],
        }


@dataclass
class NodeInstance:
    node_id: str
    node: Any
    params: Any

    @property
    def node_type(self) -> str:
        return getattr(self.node, "NODE_TYPE", "unknown")


@dataclass
class Edge:
    source: str
    target: str
    input_slot: int = 0


class DAG:
    """노드 기반 퀀트 파이프라인 실행 그래프."""

    # AND/OR 계열은 2개 이상 입력을 허용합니다.
    _VARIADIC_TYPES = {"and", "or", "and_filter", "or_filter"}

    def __init__(self, name: str = ""):
        self.name = name
        self._nodes: dict[str, NodeInstance] = {}
        self._edges: list[Edge] = []

    # ── Build API ────────────────────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    @property
    def node_ids(self) -> list[str]:
        return list(self._nodes.keys())

    def add_node(self, node_id: str, node: Any, params: Any | None = None) -> "DAG":
        if node_id in self._nodes:
            raise DAGValidationError(f"중복 node_id: {node_id}")

        try:
            if hasattr(node, "validate_params") and not isinstance(params, BaseModel):
                params = node.validate_params(params if isinstance(params, dict) or params is None else params)
        except ValidationError as exc:
            raise DAGValidationError(f"파라미터 검증 실패 ({node_id}): {exc}") from exc
        except Exception as exc:
            raise DAGValidationError(f"파라미터 검증 실패 ({node_id}): {exc}") from exc

        self._nodes[node_id] = NodeInstance(node_id, node, params)
        return self

    def add_edge(self, source_id: str, target_id: str, input_slot: int | None = None) -> "DAG":
        if source_id == target_id:
            raise DAGValidationError("자기 참조 엣지는 허용되지 않습니다.")
        if source_id not in self._nodes:
            raise DAGValidationError(f"존재하지 않는 source node: {source_id}")
        if target_id not in self._nodes:
            raise DAGValidationError(f"존재하지 않는 target node: {target_id}")

        target = self._nodes[target_id]
        arity = int(getattr(target.node, "INPUT_ARITY", 0))
        if arity == 0:
            raise DAGValidationError(f"source 노드에는 입력 엣지를 연결할 수 없습니다: {target_id}")

        used_slots = {e.input_slot for e in self._edges if e.target == target_id}
        if input_slot is None:
            input_slot = 0
            while input_slot in used_slots:
                input_slot += 1

        if input_slot < 0:
            raise DAGValidationError(f"input_slot은 0 이상이어야 합니다: {input_slot}")
        if input_slot in used_slots:
            raise DAGValidationError(f"{target_id}의 input_slot={input_slot}은 이미 연결되어 있습니다.")

        if not self._is_variadic(target) and input_slot >= arity:
            raise DAGValidationError(
                f"{target_id}는 입력 {arity}개만 허용하지만 input_slot={input_slot}이 지정되었습니다."
            )

        self._edges.append(Edge(source_id, target_id, input_slot))
        return self

    # ── Graph helpers ────────────────────────────────────────────────────

    def parents(self, node_id: str) -> list[Edge]:
        return sorted(
            [e for e in self._edges if e.target == node_id],
            key=lambda e: (e.input_slot, e.source),
        )

    def children(self, node_id: str) -> list[Edge]:
        return sorted(
            [e for e in self._edges if e.source == node_id],
            key=lambda e: (e.target, e.input_slot),
        )

    def _is_variadic(self, inst: NodeInstance) -> bool:
        return inst.node_type in self._VARIADIC_TYPES

    def _ordered_node_ids(self) -> list[str]:
        return list(self._nodes.keys())

    # ── Validation ───────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        errors: list[str] = []

        # 입력 개수 / 슬롯 검증
        for node_id, inst in self._nodes.items():
            arity = int(getattr(inst.node, "INPUT_ARITY", 0))
            incoming = self.parents(node_id)

            if arity == 0:
                if incoming:
                    errors.append(f"{node_id}: source 노드는 입력을 받을 수 없습니다.")
                continue

            if len(incoming) < arity:
                errors.append(f"{node_id}: 입력 {arity}개 필요, 현재 {len(incoming)}개")

            if not self._is_variadic(inst) and len(incoming) > arity:
                errors.append(f"{node_id}: 입력 {arity}개 초과 연결")

            slots = [e.input_slot for e in incoming]
            if len(slots) != len(set(slots)):
                errors.append(f"{node_id}: 중복 입력 슬롯이 있습니다.")

        # 컬럼 호환성 검증
        for edge in self._edges:
            src = self._nodes[edge.source].node
            dst = self._nodes[edge.target].node
            provided = src.output_columns_set() if hasattr(src, "output_columns_set") else set()
            required = dst.required_columns_set() if hasattr(dst, "required_columns_set") else set()
            missing = sorted(required - provided)
            if missing:
                errors.append(
                    f"{edge.source} → {edge.target}: 필수 컬럼 누락 {missing}"
                )

        # 사이클 검증
        try:
            self._topological_sort_impl()
        except DAGValidationError as exc:
            errors.append(str(exc))

        return errors

    def _raise_if_invalid(self) -> None:
        errors = self.validate()
        if errors:
            raise DAGValidationError("; ".join(errors))

    # ── Cache keys ───────────────────────────────────────────────────────

    @staticmethod
    def _params_to_jsonable(params: Any) -> Any:
        if isinstance(params, BaseModel):
            return params.model_dump(mode="json")
        if isinstance(params, dict):
            return params
        if params is None:
            return {}
        return str(params)

    def compute_cache_key(
        self,
        node_id: str,
        as_of_date: str,
        *,
        is_single: bool = False,
        _memo: dict[str, str] | None = None,
    ) -> str:
        """노드와 상위 입력 상태로부터 결정론적 SHA256 캐시 키를 계산합니다."""
        if node_id not in self._nodes:
            raise DAGValidationError(f"존재하지 않는 노드: {node_id}")
        memo = _memo if _memo is not None else {}
        if node_id in memo:
            return memo[node_id]

        parent_keys = sorted(
            self.compute_cache_key(e.source, as_of_date, is_single=is_single, _memo=memo)
            for e in self.parents(node_id)
        )
        inst = self._nodes[node_id]
        payload = {
            "node_id": node_id,
            "node_type": inst.node_type,
            "node_class": inst.node.__class__.__name__,
            "cache_version": getattr(inst.node, "CACHE_VERSION", ""),
            "output_columns": list(getattr(inst.node, "OUTPUT_COLUMNS", ())),
            "params": self._params_to_jsonable(inst.params),
            "as_of_date": as_of_date,
            "parent_keys": parent_keys,
            "is_single": is_single,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        memo[node_id] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return memo[node_id]

    # Backward-compatible internal name.
    def _compute_cache_key(self, node_id: str, as_of_date: str, memo: dict[str, str]) -> str:
        return self.compute_cache_key(node_id, as_of_date, _memo=memo)

    # ── Execution ────────────────────────────────────────────────────────

    def execute(
        self,
        as_of_date: str,
        result_cache: Any,
        krx_client: Any = None,
        extras: dict | None = None,
        force_refresh: bool = False,
        is_single: bool = False,
        progress_callback: Any | None = None,
    ) -> ExecutionResult:
        self._raise_if_invalid()

        run_id = "run_" + uuid.uuid4().hex[:12]
        cache_dir = str(getattr(result_cache, "cache_dir", ""))
        ctx = ExecutionContext(
            as_of_date=as_of_date,
            run_id=run_id,
            cache_dir=cache_dir,
            krx_client=krx_client,
            is_single_analysis=is_single,
            extras=extras or {},
            progress_callback=progress_callback,
        )

        total_t0 = time.perf_counter()
        res = ExecutionResult(as_of_date=as_of_date)
        res.run_id = run_id

        outputs: dict[str, pd.DataFrame] = {}
        node_logs: dict[str, NodeExecutionLog] = {}
        completed_nodes: set[str] = set()
        failed_nodes: set[str] = set()
        total_nodes = max(len(self._nodes), 1)

        def emit_progress(event: dict[str, Any]) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(event)
            except Exception:
                logger.debug("progress_callback failed", exc_info=True)

        cache_memo: dict[str, str] = {}
        cache_keys = {
            node_id: self.compute_cache_key(node_id, as_of_date, is_single=is_single, _memo=cache_memo)
            for node_id in self._nodes
        }

        in_degree = {nid: len(self.parents(nid)) for nid in self._nodes}
        ready_queue = [nid for nid in self._ordered_node_ids() if in_degree[nid] == 0]

        def run_node(node_id: str):
            t0 = time.perf_counter()
            inst = self._nodes[node_id]
            log = NodeExecutionLog(
                node_id,
                inst.node_type,
                getattr(inst.node, "DISPLAY_NAME", inst.node_type),
                cache_key=cache_keys[node_id],
            )

            def enrich_log(output: pd.DataFrame | None) -> None:
                if output is None:
                    return
                log.output_count = len(output)
                log.dropped_count = max(log.input_count - log.output_count, 0)
                if log.output_count:
                    def is_auxiliary_col(col: str) -> bool:
                        lower = col.lower()
                        return lower.endswith(("_warning", "_reason", "_note", "_comment", "_message"))

                    diagnostic_cols = [c for c in output.columns if not is_auxiliary_col(str(c))]
                    na_counts = output[diagnostic_cols].isna().sum().sort_values(ascending=False)
                    log.nan_columns = [
                        {"column": str(col), "nan_count": int(count)}
                        for col, count in na_counts.head(10).items()
                        if int(count) > 0
                    ]
                    reason_cols = [
                        c for c in output.columns
                        if any(token in c.lower() for token in ("status", "flag", "warning", "reason"))
                    ]
                    reasons: list[dict[str, Any]] = []
                    for col in reason_cols:
                        vc = output[col].dropna().astype(str).value_counts().head(5)
                        for value, count in vc.items():
                            if value and value.lower() not in ("none", "nan"):
                                reasons.append({"column": col, "reason": value, "count": int(count)})
                    reasons.sort(key=lambda x: x["count"], reverse=True)
                    log.drop_reasons = reasons[:10]
                    marker_cols = [
                        c for c in output.columns
                        if any(token in c.lower() for token in ("status", "flag"))
                    ]
                    if marker_cols:
                        missing_mask = output[marker_cols].apply(
                            lambda s: s.astype(str).str.contains("DATA_MISSING|Data Missing|데이터 없음|수집 실패|UNKNOWN", case=False, na=False)
                        )
                    else:
                        missing_mask = pd.DataFrame(index=output.index)
                    score_cols = [
                        c for c in output.columns
                        if c in {
                            "rs_score", "vcp_score", "institution_flow_score",
                            "flow_score", "foreign_flow_score", "macro_score",
                            "final_score", "total_score", "close", "volume",
                        }
                    ]
                    score_missing = output[score_cols].isna() if score_cols else pd.DataFrame(index=output.index)
                    combined_missing = pd.concat([missing_mask, score_missing], axis=1) if not missing_mask.empty or not score_missing.empty else pd.DataFrame(index=output.index)
                    log.data_missing_count = int(combined_missing.any(axis=1).sum()) if not combined_missing.empty else 0
                    log.data_missing_ratio = round(log.data_missing_count / log.output_count, 4)
                elif log.input_count:
                    log.drop_reasons = [{"reason": "ALL_ROWS_DROPPED", "count": log.input_count}]
                    log.data_missing_count = 0
                    log.data_missing_ratio = 0.0

            parent_edges = self.parents(node_id)
            if any(e.source in failed_nodes for e in parent_edges):
                log.status = "skipped"
                log.error = "Parent failed"
                log.latency_ms = (time.perf_counter() - t0) * 1000
                return node_id, None, "skipped", log, log.error

            try:
                node_inputs = [outputs[e.source] for e in parent_edges]
                log.input_count = sum(len(i) for i in node_inputs)
                emit_progress({
                    "event": "node_start",
                    "node_id": node_id,
                    "node_type": inst.node_type,
                    "input_count": log.input_count,
                    "output_count": None,
                    "elapsed_ms": 0.0,
                    "cache_hit": False,
                })

                is_today = as_of_date == datetime.now().strftime("%Y-%m-%d")
                if result_cache is not None and not force_refresh and not is_today:
                    cached = result_cache.get(cache_keys[node_id])
                    if cached is not None:
                        log.status = "cache_hit"
                        log.cache_hit = True
                        enrich_log(cached)
                        log.latency_ms = (time.perf_counter() - t0) * 1000
                        logger.info(
                            "dag_node_complete",
                            extra={
                                "node_id": node_id,
                                "node_type": inst.node_type,
                                "input_count": log.input_count,
                                "output_count": log.output_count,
                                "dropped_count": log.dropped_count,
                                "elapsed_ms": round(log.latency_ms, 1),
                                "cache_hit": True,
                            },
                        )
                        return node_id, cached, "ok", log, None

                logger.info(
                    "dag_node_start",
                    extra={
                        "node_id": node_id,
                        "node_type": inst.node_type,
                        "input_count": log.input_count,
                        "output_count": None,
                        "elapsed_ms": 0.0,
                        "cache_hit": False,
                    },
                )
                result = inst.node.run(node_inputs, inst.params, ctx)
                if result is None:
                    result = pd.DataFrame()
                if not isinstance(result, pd.DataFrame):
                    raise TypeError(f"{node_id} returned {type(result).__name__}, expected DataFrame")

                log.latency_ms = (time.perf_counter() - t0) * 1000
                enrich_log(result)
                logger.info(
                    "dag_node_complete",
                    extra={
                        "node_id": node_id,
                        "node_type": inst.node_type,
                        "input_count": log.input_count,
                        "output_count": log.output_count,
                        "dropped_count": log.dropped_count,
                        "elapsed_ms": round(log.latency_ms, 1),
                        "cache_hit": False,
                    },
                )

                if result_cache is not None and not is_today:
                    result_cache.put(cache_keys[node_id], result)

                if len(result) == 0 and log.input_count > 0:
                    input_codes: list[Any] = []
                    for inp in node_inputs:
                        if "code" in inp.columns:
                            input_codes = inp["code"].tolist()[:10]
                    logger.warning(
                        "[DATA DRAIN] %s → 출력 0행 (입력 %d행). is_single=%s, 입력 코드 샘플: %s",
                        node_id,
                        log.input_count,
                        is_single,
                        input_codes,
                    )
                return node_id, result, "ok", log, None
            except Exception as exc:
                log.status = "error"
                log.error = str(exc)
                log.dropped_count = log.input_count
                log.latency_ms = (time.perf_counter() - t0) * 1000
                logger.error(
                    "dag_node_error",
                    extra={
                        "node_id": node_id,
                        "node_type": inst.node_type,
                        "input_count": log.input_count,
                        "output_count": log.output_count,
                        "dropped_count": log.dropped_count,
                        "elapsed_ms": round(log.latency_ms, 1),
                        "cache_hit": log.cache_hit,
                    },
                    exc_info=True,
                )
                return node_id, None, "error", log, str(exc)

        with ThreadPoolExecutor(max_workers=5) as executor:
            while len(completed_nodes) + len(failed_nodes) < len(self._nodes):
                if not ready_queue:
                    remaining = set(self._nodes) - completed_nodes - failed_nodes
                    if remaining:
                        res.success = False
                        res.error = f"실행 불가 노드가 남았습니다: {sorted(remaining)}"
                    break

                current_batch = ready_queue
                ready_queue = []
                futures = {executor.submit(run_node, nid): nid for nid in current_batch}

                for future in as_completed(futures):
                    nid, result, status, log, err = future.result()
                    node_logs[nid] = log

                    if status == "ok":
                        outputs[nid] = result
                        completed_nodes.add(nid)
                    else:
                        failed_nodes.add(nid)
                        res.success = False
                        if res.error is None and err:
                            res.error = err

                    done = len(completed_nodes) + len(failed_nodes)
                    emit_progress({
                        "event": "node_complete",
                        "node_id": nid,
                        "node_type": log.node_type,
                        "input_count": log.input_count,
                        "output_count": log.output_count,
                        "dropped_count": log.dropped_count,
                        "elapsed_ms": round(log.latency_ms, 1),
                        "cache_hit": log.cache_hit,
                        "drop_reasons": log.drop_reasons,
                        "data_missing_count": log.data_missing_count,
                        "data_missing_ratio": log.data_missing_ratio,
                        "nan_columns": log.nan_columns,
                        "status": log.status,
                        "error": log.error,
                        "progress": done / total_nodes,
                    })

                    for edge in self.children(nid):
                        child_id = edge.target
                        in_degree[child_id] -= 1
                        if in_degree[child_id] == 0 and child_id not in completed_nodes and child_id not in failed_nodes:
                            ready_queue.append(child_id)

        res.outputs = outputs
        topo = self.topological_sort()
        res.node_logs = [node_logs[nid] for nid in topo if nid in node_logs]
        res.total_latency_ms = (time.perf_counter() - total_t0) * 1000
        return res

    # ── Ordering / serialization ─────────────────────────────────────────

    def _topological_sort_impl(self) -> list[str]:
        in_degree = {nid: 0 for nid in self._nodes}
        for edge in self._edges:
            if edge.source not in self._nodes or edge.target not in self._nodes:
                raise DAGValidationError("존재하지 않는 노드 참조가 있습니다.")
            in_degree[edge.target] += 1

        ready = [nid for nid in self._ordered_node_ids() if in_degree[nid] == 0]
        order: list[str] = []

        while ready:
            nid = ready.pop(0)
            order.append(nid)
            for edge in self.children(nid):
                in_degree[edge.target] -= 1
                if in_degree[edge.target] == 0:
                    ready.append(edge.target)

        if len(order) != len(self._nodes):
            raise DAGValidationError("사이클이 감지되었습니다.")
        return order

    def topological_sort(self) -> list[str]:
        return self._topological_sort_impl()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "nodes": [
                {
                    "id": node_id,
                    "type": inst.node_type,
                    "params": self._params_to_jsonable(inst.params),
                }
                for node_id, inst in self._nodes.items()
            ],
            "edges": [
                {
                    "from": e.source,
                    "to": e.target,
                    "input_slot": e.input_slot,
                }
                for e in self._edges
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], registry: dict[str, type]) -> "DAG":
        dag = cls(name=data.get("name", ""))
        for nd in data.get("nodes", []):
            node_type = nd.get("type")
            if node_type not in registry:
                raise DAGValidationError(f"알 수 없는 노드 타입: {node_type}")
            dag.add_node(nd["id"], registry[node_type](), nd.get("params", {}))

        for ed in data.get("edges", []):
            source = ed.get("from", ed.get("source"))
            target = ed.get("to", ed.get("target"))
            dag.add_edge(source, target, input_slot=ed.get("input_slot"))
        return dag
