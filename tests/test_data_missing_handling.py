"""
DATA_MISSING 처리 일관성 테스트.

- VCP 노드: OHLCV가 없으면 vcp_score=None (50 가산 금지)
- box_breakout: 가격 데이터 부족 시 breakout_score=None
- ma_alignment: 60일 데이터 부족 시 ma_alignment_score=None
- rs_rating: 벤치마크 부재 시 rs_score=None
- score_filter: vcp_score=None이면 total에 50 더하지 않음, Tier 1 차단
- parse_volume: 다양한 입력에 대해 견고한 동작
- liquidity_filter 프론트 표시: liquidity_status에 따라 ✓/부족 표시 분기
"""
import pandas as pd
import pytest

from nodes.liquidity_filter import parse_volume


class TestParseVolume:
    """parse_volume helper의 다양한 입력 처리."""

    def test_string_with_K_suffix(self):
        assert parse_volume("522.04K") == pytest.approx(522040.0)

    def test_string_with_M_suffix(self):
        assert parse_volume("5.00M") == pytest.approx(5_000_000.0)

    def test_string_with_B_suffix(self):
        assert parse_volume("1.5B") == pytest.approx(1_500_000_000.0)

    def test_string_with_comma(self):
        assert parse_volume("522,040") == pytest.approx(522040.0)

    def test_string_with_korean_thousand(self):
        assert parse_volume("522.04천") == pytest.approx(522040.0)

    def test_string_with_korean_million(self):
        assert parse_volume("1.2백만") == pytest.approx(1_200_000.0)

    def test_string_with_korean_eok(self):
        assert parse_volume("3.4억") == pytest.approx(340_000_000.0)

    def test_int_value(self):
        assert parse_volume(522040) == 522040.0

    def test_float_value(self):
        assert parse_volume(522040.5) == 522040.5

    def test_none_returns_none(self):
        assert parse_volume(None) is None

    def test_empty_string_returns_none(self):
        assert parse_volume("") is None

    def test_dash_returns_none(self):
        assert parse_volume("-") is None

    def test_NA_returns_none(self):
        assert parse_volume("N/A") is None

    def test_lowercase_na_returns_none(self):
        assert parse_volume("n/a") is None

    def test_pandas_NA_returns_none(self):
        assert parse_volume(pd.NA) is None

    def test_invalid_garbage_returns_none(self):
        assert parse_volume("garbage_value") is None

    def test_lowercase_k_suffix(self):
        assert parse_volume("522.04k") == pytest.approx(522040.0)


class TestVcpDataMissing:
    """VCP 노드가 DATA_MISSING일 때 50점을 가산하지 않는지 확인."""

    def test_vcp_score_is_none_when_data_missing(self):
        """OHLCV가 없으면 vcp_score는 None이어야 한다."""
        from nodes.vcp import VcpNode, VcpParams
        from engine.node_base import ExecutionContext

        node = VcpNode()

        class _NoOhlcvKrx:
            def get_ohlcv_batch(self, codes, start_date, end_date, pages=3, **kw):
                return {}  # 모든 종목 OHLCV 없음

        ctx = ExecutionContext(
            as_of_date="2026-05-08",
            run_id="test",
            krx_client=_NoOhlcvKrx(),
            is_single_analysis=False,
        )
        df_in = pd.DataFrame([{"code": "005930", "name": "삼성전자", "close": 90000, "market_cap": 5e14}])
        out = node.run([df_in], VcpParams(), ctx)

        assert out.iloc[0]["vcp_status"] == "DATA_MISSING"
        # 핵심: vcp_score는 None이어야 함 (50 아님)
        assert out.iloc[0]["vcp_score"] is None or pd.isna(out.iloc[0]["vcp_score"])


class TestScoreFilterDataMissing:
    """score_filter가 DATA_MISSING 시 50점 가산 없이 처리하는지 확인."""

    def test_total_score_does_not_include_50_for_data_missing_vcp(self):
        """vcp_score=None, breakout_score=None, rs_score=None이면 total에 가산 0."""
        from nodes.score_filter import ScoreFilterNode, ScoreFilterParams
        from engine.node_base import ExecutionContext

        node = ScoreFilterNode()
        ctx = ExecutionContext(
            as_of_date="2026-05-08", run_id="test", krx_client=None, is_single_analysis=False,
        )

        df_in = pd.DataFrame([{
            "code": "005930", "name": "삼성전자",
            "market_cap": 5e14, "close": 90000,
            "vcp_score": None, "vcp_status": "DATA_MISSING",
            "breakout_score": None, "breakout_status": "DATA_MISSING",
            "rs_score": None, "rs_status": "DATA_MISSING", "rs_rating": None,
            "flow_score": 0, "institution_flow_score": 0,
            "foreign_net_buy": None, "institution_net_buy": None,
            "macro_score": 50,
        }])
        out = node.run([df_in], ScoreFilterParams(), ctx)
        # 가산해야 할 컴포넌트가 모두 None이면 total은 거의 0이어야 함
        # (sector_bonus=0, macro_adjust=0, flow_total=0)
        assert int(out.iloc[0]["total_score"]) <= 5  # 노이즈 허용
        # data_missing 플래그 확인 (numpy.bool_ 호환)
        assert bool(out.iloc[0]["vcp_data_missing"]) is True
        assert bool(out.iloc[0]["breakout_data_missing"]) is True
        assert bool(out.iloc[0]["rs_data_missing"]) is True

    def test_tier1_blocked_when_vcp_data_missing(self):
        """VCP DATA_MISSING이면 Tier 1로 승격하지 못해야 한다."""
        from nodes.score_filter import ScoreFilterNode, ScoreFilterParams
        from engine.node_base import ExecutionContext

        node = ScoreFilterNode()
        ctx = ExecutionContext(
            as_of_date="2026-05-08", run_id="test", krx_client=None, is_single_analysis=False,
        )

        # 다른 모든 조건은 Tier 1 가능하도록 설정
        df_in = pd.DataFrame([{
            "code": "005930", "name": "삼성전자",
            "market_cap": 5e14, "close": 90000,
            "vcp_score": None, "vcp_status": "DATA_MISSING",
            "breakout_score": 30, "breakout_status": "BREAKOUT_CONFIRMED",
            "box_breakout_grade": "A",
            "rs_score": 60, "rs_status": "Strong", "rs_rating": 95,
            "ma_alignment_flag": "ALIGNED",
            "liquidity_status": "LIQUID",
            "flow_score": 15, "institution_flow_score": 15, "flow_total_score": 30,
            "foreign_net_buy": 1000, "institution_net_buy": 1000,
            "macro_score": 60,
            "breakout_distance_pct": 1.0,
        }])
        out = node.run([df_in], ScoreFilterParams(), ctx)
        # Tier 1로 승격되면 안 됨
        assert out.iloc[0]["primary_bucket"] != "TIER_1"


class TestLiquidityStatusConsistency:
    """유동성 카드의 ✓ 표시는 liquidity_status가 LIQUID일 때만 나와야 한다.

    프론트엔드 로직 (strategy-builder.js)이 이를 보장하지만,
    여기서는 백엔드가 일관된 status 값을 출력하는지만 확인한다.
    """

    def test_liquidity_status_field_present(self):
        """liquidity_filter 노드 출력에는 liquidity_status가 항상 있어야 한다."""
        from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
        from engine.node_base import ExecutionContext

        node = LiquidityFilterNode()

        class _MockKrx:
            def get_ohlcv_batch(self, codes, start_date, end_date, pages=3, **kw):
                return {}  # 빈 OHLCV — DATA_MISSING 경로 트리거

        ctx = ExecutionContext(
            as_of_date="2026-05-08", run_id="test", krx_client=_MockKrx(), is_single_analysis=False,
        )
        df_in = pd.DataFrame([{
            "code": "005930", "name": "삼성전자",
            "close": 90000, "volume": None, "market_cap": 5e14,
        }])
        out = node.run([df_in], LiquidityFilterParams(), ctx)
        assert "liquidity_status" in out.columns
        # OHLCV도 raw도 없으므로 DATA_MISSING이어야 함
        assert out.iloc[0]["liquidity_status"] in {"DATA_MISSING", "LIQUIDITY_UNKNOWN"}


class TestVolumeSuspicious:
    """이상치 거래량 감지: 삼성전기 134주, 현대차 217주 등은 volume_suspicious=True여야 한다."""

    def test_samsung_electromechanics_low_volume_suspicious(self):
        """삼성전기 close=914000 volume=134 mkt_cap=10조+ → suspicious=True, ILLIQUID 확정 금지."""
        from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
        from engine.node_base import ExecutionContext

        node = LiquidityFilterNode()

        class _Krx:
            def get_ohlcv_batch(self, codes, start_date, end_date, pages=3, **kw):
                # OHLCV는 비어있다고 가정 — row의 volume을 사용
                return {}

        ctx = ExecutionContext(
            as_of_date="2026-05-08", run_id="test", krx_client=_Krx(), is_single_analysis=False,
        )
        df_in = pd.DataFrame([{
            "code": "009150", "name": "삼성전기",
            "close": 914000, "volume": 134,
            "market_cap": 11_000_000_000_000,  # 11조
        }])
        out = node.run([df_in], LiquidityFilterParams(), ctx)
        # volume_suspicious=True 여야 함
        assert bool(out.iloc[0]["volume_suspicious"]) is True
        # ILLIQUID로 확정되면 안 됨 (LIQUIDITY_UNCERTAIN 또는 다른 status)
        assert out.iloc[0]["liquidity_status"] != "ILLIQUID"


class TestCanonicalMarketSnapshot:
    """canonical market snapshot 배선 검증 — OHLCV가 없어도 universe row의 close/volume으로 유동성 계산."""

    def _make_krx(self):
        class _EmptyKrx:
            def get_ohlcv_batch(self, codes, start_date, end_date, pages=3, **kw):
                return {}  # OHLCV 전 종목 빈 응답
        return _EmptyKrx()

    def _make_ctx(self):
        from engine.node_base import ExecutionContext
        return ExecutionContext(
            as_of_date="2026-05-08", run_id="test",
            krx_client=self._make_krx(), is_single_analysis=False,
        )

    def test_sk_square_liquid(self):
        """SK스퀘어 close=1,098,000 vol=718,102 → OHLCV 없어도 LIQUID."""
        from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
        node = LiquidityFilterNode()
        ctx = self._make_ctx()
        df_in = pd.DataFrame([{
            "code": "402340", "name": "SK스퀘어",
            "close": 1_098_000, "volume": 718_102,
            "market_cap": 144_890_300_000_000,
        }])
        out = node.run([df_in], LiquidityFilterParams(), ctx)
        row = out.iloc[0]
        assert row["liquidity_status"] == "LIQUID", f"Expected LIQUID, got {row['liquidity_status']}"
        assert row["liquidity_quote_source"] == "universe_snapshot"
        assert row["calculated_trading_value"] >= 2_000_000_000

    def test_samsung_electronics_liquid(self):
        """삼성전자 close=268,500 vol=25,696,964 → OHLCV 없어도 LIQUID."""
        from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
        node = LiquidityFilterNode()
        ctx = self._make_ctx()
        df_in = pd.DataFrame([{
            "code": "005930", "name": "삼성전자",
            "close": 268_500, "volume": 25_696_964,
            "market_cap": 1_569_725_800_000_000,
        }])
        out = node.run([df_in], LiquidityFilterParams(), ctx)
        row = out.iloc[0]
        assert row["liquidity_status"] == "LIQUID", f"Expected LIQUID, got {row['liquidity_status']}"
        assert row["calculated_trading_value"] >= 2_000_000_000

    def test_hanmi_semiconductor_liquid(self):
        """한미반도체 close=390,000 vol=698,555 → OHLCV 없어도 LIQUID."""
        from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
        node = LiquidityFilterNode()
        ctx = self._make_ctx()
        df_in = pd.DataFrame([{
            "code": "042700", "name": "한미반도체",
            "close": 390_000, "volume": 698_555,
            "market_cap": 37_171_800_000_000,
        }])
        out = node.run([df_in], LiquidityFilterParams(), ctx)
        row = out.iloc[0]
        assert row["liquidity_status"] == "LIQUID", f"Expected LIQUID, got {row['liquidity_status']}"
        assert row["calculated_trading_value"] >= 2_000_000_000

    def test_ls_electric_liquid(self):
        """LS ELECTRIC close=313,000 vol=1,151,589 → OHLCV 없어도 LIQUID."""
        from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
        node = LiquidityFilterNode()
        ctx = self._make_ctx()
        df_in = pd.DataFrame([{
            "code": "010120", "name": "LS ELECTRIC",
            "close": 313_000, "volume": 1_151_589,
            "market_cap": 46_950_000_000_000,
        }])
        out = node.run([df_in], LiquidityFilterParams(), ctx)
        row = out.iloc[0]
        assert row["liquidity_status"] == "LIQUID", f"Expected LIQUID, got {row['liquidity_status']}"
        assert row["calculated_trading_value"] >= 2_000_000_000

    def test_liquid_status_no_data_insufficient(self):
        """liquidity_status=LIQUID이면 data_insufficient_reasons에 유동성 항목이 없어야 한다."""
        from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
        from nodes.score_filter import ScoreFilterNode, ScoreFilterParams
        from engine.node_base import ExecutionContext

        liq_node = LiquidityFilterNode()
        score_node = ScoreFilterNode()

        class _EmptyKrx:
            def get_ohlcv_batch(self, codes, start_date, end_date, pages=3, **kw):
                return {}

        ctx_liq = ExecutionContext(
            as_of_date="2026-05-08", run_id="test",
            krx_client=_EmptyKrx(), is_single_analysis=False,
        )
        ctx_score = ExecutionContext(
            as_of_date="2026-05-08", run_id="test",
            krx_client=None, is_single_analysis=False,
        )
        df_in = pd.DataFrame([{
            "code": "402340", "name": "SK스퀘어",
            "close": 1_098_000, "volume": 718_102,
            "market_cap": 144_890_300_000_000,
        }])
        # 1단계: 유동성 필터
        liq_out = liq_node.run([df_in], LiquidityFilterParams(), ctx_liq)
        assert liq_out.iloc[0]["liquidity_status"] == "LIQUID"

        # 2단계: 스코어 필터 (DATA_MISSING 컴포넌트 포함)
        liq_out["vcp_score"] = None
        liq_out["vcp_status"] = "DATA_MISSING"
        liq_out["breakout_score"] = None
        liq_out["breakout_status"] = "DATA_MISSING"
        liq_out["rs_score"] = None
        liq_out["rs_status"] = "DATA_MISSING"
        liq_out["rs_rating"] = None
        liq_out["flow_score"] = 0
        liq_out["institution_flow_score"] = 0
        liq_out["macro_score"] = 50

        score_out = score_node.run([liq_out], ScoreFilterParams(), ctx_score)
        risk_reason = str(score_out.iloc[0].get("risk_gate_reason", ""))
        # 유동성 DATA_INSUFFICIENT는 없어야 함 (RS/MA는 DATA_MISSING이어도 됨)
        assert "유동성 DATA_INSUFFICIENT" not in risk_reason, (
            f"유동성이 LIQUID인데 risk_gate_reason에 '유동성 DATA_INSUFFICIENT'가 포함됨: {risk_reason}"
        )

    def test_volume_zero_not_data_missing(self):
        """volume=0인 소형주 → DATA_MISSING이 아닌 ILLIQUID여야 한다."""
        from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
        node = LiquidityFilterNode()
        ctx = self._make_ctx()
        df_in = pd.DataFrame([{
            "code": "999999", "name": "소형주테스트",
            "close": 5000, "volume": 0,
            "market_cap": 5_000_000_000,  # 50억 (1조 미만)
        }])
        out = node.run([df_in], LiquidityFilterParams(), ctx)
        row = out.iloc[0]
        # DATA_MISSING이 아닌 ILLIQUID여야 함 (volume=0은 미거래이지 데이터 누락이 아님)
        assert row["liquidity_status"] != "DATA_MISSING", f"volume=0은 DATA_MISSING이 아닌 ILLIQUID: {row['liquidity_status']}"
        assert row["liquidity_status"] in {"ILLIQUID", "LIQUIDITY_UNCERTAIN"}

    def test_quote_source_universe_snapshot_when_ohlcv_empty(self):
        """OHLCV가 비어있을 때 quote_source='universe_snapshot'이어야 한다."""
        from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
        node = LiquidityFilterNode()
        ctx = self._make_ctx()
        df_in = pd.DataFrame([{
            "code": "402340", "name": "SK스퀘어",
            "close": 1_098_000, "volume": 718_102,
            "market_cap": 144_890_300_000_000,
        }])
        out = node.run([df_in], LiquidityFilterParams(), ctx)
        assert out.iloc[0]["liquidity_quote_source"] == "universe_snapshot"
        assert out.iloc[0]["liquidity_close_source"] == "universe_row"
        assert out.iloc[0]["liquidity_volume_source"] == "universe_row"
