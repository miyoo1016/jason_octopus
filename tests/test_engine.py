"""
DAG 엔진 종합 테스트.

검증 항목:
  - 노드 추가·중복·자기참조·미존재 참조
  - 사이클 탐지 (단순·간접·자기 참조)
  - 위상 정렬 결정성
  - 컬럼 호환성 검증 (REQUIRED ⊆ provided + STANDARD)
  - 입력 슬롯 충족도 (다중 입력 노드)
  - 캐시 키 결정성 (동일 입력 → 동일 키)
  - 캐시 키 invalidation (params 변경 시 자기 + 하위 노드 키 변경)
  - 캐시 히트 / 미스
  - 부분 재실행 (params 일부만 변경 시 영향 받는 노드만 재실행)
  - 실패 격리 (한 분기 실패 시 다른 분기는 정상 실행)
  - 실행 결과 직렬화 / DAG 직렬화

실행:
    pytest tests/test_engine.py -v
"""
from __future__ import annotations

import pandas as pd
import pytest
from pydantic import BaseModel, Field

from engine.cache import ResultCache
from engine.dag import DAG, DAGValidationError, ExecutionResult
from engine.node_base import BaseNode, EmptyParams, ExecutionContext


# ══════════════════════════════════════════════════════════════════════════════
# 테스트용 모의 노드 정의
# ══════════════════════════════════════════════════════════════════════════════

class ConstantParams(BaseModel):
    n: int = 3
    base: float = 0.0


class ConstantNode(BaseNode):
    """입력 없이 n개 행을 생성하는 소스 노드."""
    NODE_TYPE      = "constant"
    DISPLAY_NAME   = "상수 노드"
    INPUT_ARITY    = 0
    OUTPUT_COLUMNS = ("value",)
    ParamsModel    = ConstantParams

    def run(self, inputs, params, context):
        return pd.DataFrame({
            "code":   [f"{i:06d}" for i in range(params.n)],
            "name":   [f"종목{i}" for i in range(params.n)],
            "market": ["KOSPI"] * params.n,
            "close":  [1000.0 + i for i in range(params.n)] if params.n else [],
            "volume": [1000] * params.n,
            "value":  [params.base + i * 10.0 for i in range(params.n)],
        })


class FilterParams(BaseModel):
    threshold: float = 5.0


class FilterNode(BaseNode):
    """value >= threshold 통과 필터."""
    NODE_TYPE              = "filter"
    DISPLAY_NAME           = "임계값 필터"
    INPUT_ARITY            = 1
    REQUIRED_INPUT_COLUMNS = ("value",)
    OUTPUT_COLUMNS         = ("value",)
    ParamsModel            = FilterParams

    def run(self, inputs, params, context):
        df = inputs[0]
        return df[df["value"] >= params.threshold].reset_index(drop=True)


class AndNode(BaseNode):
    """두 입력의 code 교집합."""
    NODE_TYPE              = "and"
    DISPLAY_NAME           = "AND 결합"
    INPUT_ARITY            = 2
    REQUIRED_INPUT_COLUMNS = ()
    OUTPUT_COLUMNS         = ()
    ParamsModel            = EmptyParams

    def run(self, inputs, params, context):
        left, right = inputs[0], inputs[1]
        codes = set(left["code"]) & set(right["code"])
        return left[left["code"].isin(codes)].reset_index(drop=True)


class FailingNode(BaseNode):
    """항상 실패하는 노드 (실패 격리 테스트용)."""
    NODE_TYPE              = "failing"
    DISPLAY_NAME           = "실패 노드"
    INPUT_ARITY            = 1
    REQUIRED_INPUT_COLUMNS = ()
    OUTPUT_COLUMNS         = ("value",)
    ParamsModel            = EmptyParams

    def run(self, inputs, params, context):
        raise RuntimeError("의도된 실패")


class NeedsValueParams(BaseModel):
    pass


class NeedsValueNode(BaseNode):
    """REQUIRED_INPUT_COLUMNS가 'value'인 노드 (호환성 검증용)."""
    NODE_TYPE              = "needs_value"
    DISPLAY_NAME           = "value 필요 노드"
    INPUT_ARITY            = 1
    REQUIRED_INPUT_COLUMNS = ("value",)
    OUTPUT_COLUMNS         = ("value",)
    ParamsModel            = NeedsValueParams

    def run(self, inputs, params, context):
        return inputs[0]


class OnlyStandardNode(BaseNode):
    """OUTPUT_COLUMNS가 비어있는 (표준 컬럼만 출력하는) 소스."""
    NODE_TYPE      = "only_standard"
    DISPLAY_NAME   = "표준만"
    INPUT_ARITY    = 0
    OUTPUT_COLUMNS = ()
    ParamsModel    = EmptyParams

    def run(self, inputs, params, context):
        return pd.DataFrame({
            "code":   ["000001", "000002"],
            "name":   ["A", "B"],
            "market": ["KOSPI", "KOSPI"],
            "close":  [100.0, 200.0],
            "volume": [10, 20],
        })


# ══════════════════════════════════════════════════════════════════════════════
# 픽스처
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def cache(tmp_path):
    return ResultCache(tmp_path / "results")


# ══════════════════════════════════════════════════════════════════════════════
# 1. 빌드 API 테스트
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildAPI:
    def test_add_node(self):
        dag = DAG("test")
        dag.add_node("c1", ConstantNode(), {"n": 5})
        assert dag.node_count == 1
        assert "c1" in dag.node_ids

    def test_add_node_returns_self(self):
        dag = DAG("test")
        result = dag.add_node("c1", ConstantNode())
        assert result is dag

    def test_duplicate_node_id_raises(self):
        dag = DAG()
        dag.add_node("c1", ConstantNode())
        with pytest.raises(DAGValidationError, match="중복"):
            dag.add_node("c1", ConstantNode())

    def test_invalid_params_raises(self):
        dag = DAG()
        with pytest.raises(DAGValidationError, match="파라미터 검증"):
            dag.add_node("c1", ConstantNode(), {"n": "not_a_number"})

    def test_add_edge(self):
        dag = DAG()
        dag.add_node("a", ConstantNode())
        dag.add_node("b", FilterNode())
        dag.add_edge("a", "b")
        assert dag.edge_count == 1

    def test_self_loop_raises(self):
        dag = DAG()
        dag.add_node("a", ConstantNode())
        with pytest.raises(DAGValidationError, match="자기 참조"):
            dag.add_edge("a", "a")

    def test_edge_to_nonexistent_raises(self):
        dag = DAG()
        dag.add_node("a", ConstantNode())
        with pytest.raises(DAGValidationError, match="존재하지 않는 target"):
            dag.add_edge("a", "ghost")

    def test_duplicate_slot_raises(self):
        """동일 (target, slot)에 두 엣지 연결 금지."""
        dag = DAG()
        dag.add_node("a", ConstantNode())
        dag.add_node("b", ConstantNode())
        dag.add_node("f", FilterNode())
        dag.add_edge("a", "f", input_slot=0)
        with pytest.raises(DAGValidationError, match="이미 연결"):
            dag.add_edge("b", "f", input_slot=0)


# ══════════════════════════════════════════════════════════════════════════════
# 2. 검증 테스트
# ══════════════════════════════════════════════════════════════════════════════

class TestValidation:
    def test_simple_valid(self):
        dag = DAG()
        dag.add_node("a", ConstantNode())
        dag.add_node("f", FilterNode())
        dag.add_edge("a", "f")
        assert dag.validate() == []

    def test_cycle_detection_direct(self):
        dag = DAG()
        dag.add_node("a", FilterNode())
        dag.add_node("b", FilterNode())
        dag.add_edge("a", "b")
        dag.add_edge("b", "a")
        errors = dag.validate()
        assert any("사이클" in e for e in errors)

    def test_cycle_detection_indirect(self):
        """A → B → C → A 같은 간접 사이클."""
        dag = DAG()
        dag.add_node("a", FilterNode())
        dag.add_node("b", FilterNode())
        dag.add_node("c", FilterNode())
        dag.add_edge("a", "b")
        dag.add_edge("b", "c")
        dag.add_edge("c", "a")
        errors = dag.validate()
        assert any("사이클" in e for e in errors)

    def test_missing_input_slot(self):
        """AND 노드(arity=2)에 1개만 연결되면 에러."""
        dag = DAG()
        dag.add_node("a", ConstantNode())
        dag.add_node("b", ConstantNode())
        dag.add_node("and", AndNode())
        dag.add_edge("a", "and", input_slot=0)
        # slot 1이 비어있음
        errors = dag.validate()
        assert any("입력" in e and "필요" in e for e in errors)

    def test_missing_required_column(self):
        """upstream이 'value'를 출력하지 않는데 downstream이 요구 → 에러."""
        dag = DAG()
        dag.add_node("a", OnlyStandardNode())  # value 없음
        dag.add_node("nv", NeedsValueNode())   # value 필요
        dag.add_edge("a", "nv")
        errors = dag.validate()
        assert any("컬럼" in e and "value" in e for e in errors)

    def test_standard_columns_implicitly_provided(self):
        """code/name/close 등 표준 컬럼은 upstream이 명시 안 해도 제공된 것으로 간주."""
        dag = DAG()
        dag.add_node("a", OnlyStandardNode())   # OUTPUT_COLUMNS = ()
        dag.add_node("and", AndNode())
        dag.add_node("b", OnlyStandardNode())
        dag.add_edge("a", "and", input_slot=0)
        dag.add_edge("b", "and", input_slot=1)
        # AND는 code만 필요한데, OnlyStandardNode의 OUTPUT_COLUMNS=()라도
        # code는 STANDARD_COLUMNS에 포함되어 있으므로 통과해야 함
        assert dag.validate() == []

    def test_disconnected_node_ok(self):
        """연결되지 않은 노드는 검증에서 통과해야 함 (소스라면 단독 실행 가능)."""
        dag = DAG()
        dag.add_node("a", ConstantNode())
        dag.add_node("b", ConstantNode())
        # 둘 다 INPUT_ARITY=0이므로 엣지 없어도 OK
        assert dag.validate() == []


# ══════════════════════════════════════════════════════════════════════════════
# 3. 위상 정렬 테스트
# ══════════════════════════════════════════════════════════════════════════════

class TestTopologicalSort:
    def test_linear(self):
        dag = DAG()
        dag.add_node("a", ConstantNode())
        dag.add_node("b", FilterNode())
        dag.add_node("c", FilterNode())
        dag.add_edge("a", "b")
        dag.add_edge("b", "c")
        order = dag.topological_sort()
        assert order == ["a", "b", "c"]

    def test_diamond(self):
        """     A
              ↙ ↘
             B   C
              ↘ ↙
               D
        """
        dag = DAG()
        dag.add_node("a", ConstantNode())
        dag.add_node("b", FilterNode())
        dag.add_node("c", FilterNode())
        dag.add_node("d", AndNode())
        dag.add_edge("a", "b")
        dag.add_edge("a", "c")
        dag.add_edge("b", "d", input_slot=0)
        dag.add_edge("c", "d", input_slot=1)
        order = dag.topological_sort()
        # a는 첫째, d는 마지막, b/c는 중간
        assert order[0]  == "a"
        assert order[-1] == "d"
        assert set(order[1:3]) == {"b", "c"}

    def test_deterministic(self):
        """두 번 호출해도 동일한 순서를 반환해야 함."""
        dag = DAG()
        for nid in ["c", "b", "a", "d"]:
            dag.add_node(nid, ConstantNode())
        order1 = dag.topological_sort()
        order2 = dag.topological_sort()
        assert order1 == order2

    def test_cycle_raises(self):
        dag = DAG()
        dag.add_node("a", FilterNode())
        dag.add_node("b", FilterNode())
        dag.add_edge("a", "b")
        dag.add_edge("b", "a")
        with pytest.raises(DAGValidationError):
            dag.topological_sort()


# ══════════════════════════════════════════════════════════════════════════════
# 4. 캐시 키 테스트
# ══════════════════════════════════════════════════════════════════════════════

class TestCacheKey:
    def _build_simple(self) -> DAG:
        dag = DAG()
        dag.add_node("a", ConstantNode(), {"n": 5})
        dag.add_node("f", FilterNode(),   {"threshold": 10.0})
        dag.add_edge("a", "f")
        return dag

    def test_deterministic(self):
        """동일 DAG에서 동일 노드의 키가 항상 같아야 한다."""
        dag = self._build_simple()
        k1 = dag.compute_cache_key("f", "2026-05-05")
        k2 = dag.compute_cache_key("f", "2026-05-05")
        assert k1 == k2

    def test_changes_with_params(self):
        """params 변경 → 키 변경."""
        d1 = DAG()
        d1.add_node("a", ConstantNode(), {"n": 5})
        d2 = DAG()
        d2.add_node("a", ConstantNode(), {"n": 6})

        k1 = d1.compute_cache_key("a", "2026-05-05")
        k2 = d2.compute_cache_key("a", "2026-05-05")
        assert k1 != k2

    def test_changes_with_as_of_date(self):
        dag = self._build_simple()
        k1 = dag.compute_cache_key("a", "2026-05-05")
        k2 = dag.compute_cache_key("a", "2026-05-06")
        assert k1 != k2

    def test_changes_with_node_type(self):
        d1 = DAG()
        d1.add_node("a", ConstantNode())
        d2 = DAG()
        d2.add_node("a", OnlyStandardNode())
        k1 = d1.compute_cache_key("a", "2026-05-05")
        k2 = d2.compute_cache_key("a", "2026-05-05")
        assert k1 != k2

    def test_upstream_change_propagates(self):
        """A의 params 변경 → A의 키 변경 → F(A의 자식)의 키도 변경되어야 함."""
        d1 = DAG()
        d1.add_node("a", ConstantNode(), {"n": 5})
        d1.add_node("f", FilterNode(), {"threshold": 10.0})
        d1.add_edge("a", "f")

        d2 = DAG()
        d2.add_node("a", ConstantNode(), {"n": 6})   # 다른 params
        d2.add_node("f", FilterNode(), {"threshold": 10.0})  # 동일 params
        d2.add_edge("a", "f")

        k_f1 = d1.compute_cache_key("f", "2026-05-05")
        k_f2 = d2.compute_cache_key("f", "2026-05-05")
        assert k_f1 != k_f2  # F의 params는 같지만 upstream 변경으로 키 달라야 함

    def test_multi_input_order_invariant(self):
        """다중 입력 노드: 부모 키 정렬 후 결합 → 부모 추가 순서 무관."""
        # AND(A, B)와 AND(B, A)는 다른 슬롯이지만 캐시 키는 같아야 한다 (수학적 교환법칙).
        # 우리 구현은 부모 ID를 정렬하므로 같다.
        d1 = DAG()
        d1.add_node("a", ConstantNode(), {"n": 3})
        d1.add_node("b", ConstantNode(), {"n": 4})
        d1.add_node("and", AndNode())
        d1.add_edge("a", "and", input_slot=0)
        d1.add_edge("b", "and", input_slot=1)

        d2 = DAG()
        d2.add_node("a", ConstantNode(), {"n": 3})
        d2.add_node("b", ConstantNode(), {"n": 4})
        d2.add_node("and", AndNode())
        d2.add_edge("b", "and", input_slot=0)   # 슬롯 바뀜
        d2.add_edge("a", "and", input_slot=1)

        k1 = d1.compute_cache_key("and", "2026-05-05")
        k2 = d2.compute_cache_key("and", "2026-05-05")
        assert k1 == k2


# ══════════════════════════════════════════════════════════════════════════════
# 5. 실행 테스트
# ══════════════════════════════════════════════════════════════════════════════

class TestExecution:
    def test_simple_linear_run(self, cache):
        dag = DAG()
        dag.add_node("a", ConstantNode(), {"n": 10})
        dag.add_node("f", FilterNode(), {"threshold": 50.0})
        dag.add_edge("a", "f")

        result = dag.execute(as_of_date="2026-05-05", result_cache=cache)

        assert result.success
        assert "a" in result.outputs
        assert "f" in result.outputs
        assert len(result.outputs["a"]) == 10
        # value = i*10이므로 50 이상은 [50, 60, 70, 80, 90] = 5개
        assert len(result.outputs["f"]) == 5

    def test_cache_hit_on_second_run(self, cache):
        """첫 실행 후 캐시 저장 → 두 번째 실행은 모두 cache_hit."""
        dag = DAG()
        dag.add_node("a", ConstantNode(), {"n": 5})
        dag.add_node("f", FilterNode(), {"threshold": 0.0})
        dag.add_edge("a", "f")

        r1 = dag.execute("2026-05-05", cache)
        assert all(log.status == "ok" for log in r1.node_logs)

        r2 = dag.execute("2026-05-05", cache)
        assert all(log.status == "cache_hit" for log in r2.node_logs)
        assert r2.cache_hit_rate == 1.0

    def test_force_refresh_bypasses_cache(self, cache):
        dag = DAG()
        dag.add_node("a", ConstantNode(), {"n": 5})
        dag.execute("2026-05-05", cache)
        r2 = dag.execute("2026-05-05", cache, force_refresh=True)
        assert r2.node_logs[0].status == "ok"   # cache_hit이 아님
        assert r2.node_logs[0].cache_hit is False

    def test_partial_re_execution(self, cache):
        """A → B → C 빌드 후 첫 실행 → B의 params만 변경 → A는 캐시, B/C는 재실행."""
        # 첫 빌드
        d1 = DAG()
        d1.add_node("a", ConstantNode(), {"n": 10})
        d1.add_node("b", FilterNode(), {"threshold": 30.0})
        d1.add_node("c", FilterNode(), {"threshold": 0.0})
        d1.add_edge("a", "b")
        d1.add_edge("b", "c")
        d1.execute("2026-05-05", cache)

        # 두 번째 빌드: B의 params만 변경
        d2 = DAG()
        d2.add_node("a", ConstantNode(), {"n": 10})
        d2.add_node("b", FilterNode(), {"threshold": 50.0})  # ← 변경
        d2.add_node("c", FilterNode(), {"threshold": 0.0})
        d2.add_edge("a", "b")
        d2.add_edge("b", "c")
        r2 = d2.execute("2026-05-05", cache)

        log_map = {log.node_id: log for log in r2.node_logs}
        assert log_map["a"].status == "cache_hit"   # A는 캐시 그대로
        assert log_map["b"].status == "ok"          # B는 params 변경 → 재실행
        assert log_map["c"].status == "ok"          # C는 upstream 변경 → 재실행

    def test_failure_isolation(self, cache):
        """A → Failing → C 일 때 Failing 실패해도 다른 분기는 정상."""
        dag = DAG()
        dag.add_node("a", ConstantNode(), {"n": 5})
        dag.add_node("fail", FailingNode())
        dag.add_node("post", FilterNode(), {"threshold": 0.0})
        dag.add_node("ok_branch", FilterNode(), {"threshold": 0.0})
        dag.add_edge("a", "fail")
        dag.add_edge("fail", "post")
        dag.add_edge("a", "ok_branch")

        result = dag.execute("2026-05-05", cache)

        log_map = {log.node_id: log for log in result.node_logs}
        assert log_map["a"].status         == "ok"
        assert log_map["fail"].status      == "error"
        assert "의도된 실패" in log_map["fail"].error
        assert log_map["post"].status      == "skipped"      # fail의 자식
        assert log_map["ok_branch"].status == "ok"           # 다른 분기 정상
        assert result.success is False                        # 전체적으로는 실패

    def test_diamond_execution(self, cache):
        """A → (B, C) → D(AND) 흐름."""
        dag = DAG()
        dag.add_node("a", ConstantNode(), {"n": 10})
        dag.add_node("b", FilterNode(), {"threshold": 30.0})  # value≥30 → 7개
        dag.add_node("c", FilterNode(), {"threshold": 50.0})  # value≥50 → 5개
        dag.add_node("d", AndNode())
        dag.add_edge("a", "b")
        dag.add_edge("a", "c")
        dag.add_edge("b", "d", input_slot=0)
        dag.add_edge("c", "d", input_slot=1)

        result = dag.execute("2026-05-05", cache)
        assert result.success
        # B ∩ C = C (B가 더 큰 집합) → 5개
        assert len(result.outputs["d"]) == 5

    def test_validation_error_before_execution(self, cache):
        """검증 실패 시 execute() 자체가 예외를 던져야 함."""
        dag = DAG()
        dag.add_node("a", FilterNode())
        dag.add_node("b", FilterNode())
        dag.add_edge("a", "b")
        dag.add_edge("b", "a")
        with pytest.raises(DAGValidationError):
            dag.execute("2026-05-05", cache)


# ══════════════════════════════════════════════════════════════════════════════
# 6. 직렬화 테스트
# ══════════════════════════════════════════════════════════════════════════════

class TestSerialization:
    def test_to_dict_roundtrip(self):
        d1 = DAG("my_dag")
        d1.add_node("a", ConstantNode(), {"n": 7})
        d1.add_node("f", FilterNode(), {"threshold": 12.5})
        d1.add_edge("a", "f")

        data = d1.to_dict()
        assert data["name"] == "my_dag"
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1

        registry = {"constant": ConstantNode, "filter": FilterNode}
        d2 = DAG.from_dict(data, registry)
        assert d2.name == "my_dag"
        assert d2.node_count == 2
        # 키도 동일해야 한다 (params 보존 확인)
        assert d1.compute_cache_key("f", "2026-05-05") == d2.compute_cache_key("f", "2026-05-05")

    def test_unknown_node_type_raises(self):
        data = {
            "name": "x",
            "nodes": [{"id": "a", "type": "unknown_type", "params": {}}],
            "edges": [],
        }
        with pytest.raises(DAGValidationError, match="알 수 없는"):
            DAG.from_dict(data, {})

    def test_execution_result_summary(self, tmp_path):
        cache = ResultCache(tmp_path / "results")
        dag = DAG()
        dag.add_node("a", ConstantNode(), {"n": 3})
        result = dag.execute("2026-05-05", cache)
        summary = result.summary()
        assert summary["success"] is True
        assert "node_logs" in summary
        assert summary["as_of_date"] == "2026-05-05"


# ══════════════════════════════════════════════════════════════════════════════
# 7. ResultCache 단독 테스트
# ══════════════════════════════════════════════════════════════════════════════

class TestResultCache:
    def test_put_and_get(self, tmp_path):
        cache = ResultCache(tmp_path)
        df = pd.DataFrame({"a": [1, 2, 3]})
        cache.put("key1", df)
        loaded = cache.get("key1")
        assert loaded is not None
        assert list(loaded["a"]) == [1, 2, 3]

    def test_get_miss_returns_none(self, tmp_path):
        cache = ResultCache(tmp_path)
        assert cache.get("missing") is None

    def test_empty_df_not_cached(self, tmp_path):
        cache = ResultCache(tmp_path)
        cache.put("k", pd.DataFrame())
        assert cache.get("k") is None

    def test_clear_all(self, tmp_path):
        cache = ResultCache(tmp_path)
        cache.put("a", pd.DataFrame({"x": [1]}))
        cache.put("b", pd.DataFrame({"x": [2]}))
        n = cache.clear_all()
        assert n == 2
        assert cache.keys() == []
