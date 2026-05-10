import pytest
import pandas as pd
import numpy as np
from nodes.liquidity_filter import LiquidityFilterNode, LiquidityFilterParams
from nodes.score_filter import ScoreFilterNode, ScoreFilterParams
from engine.node_base import ExecutionContext

class MockKRX:
    def __init__(self, ohlcv_data=None):
        self.ohlcv_data = ohlcv_data or {}
    def get_ohlcv_batch(self, codes, start_date, end_date):
        return self.ohlcv_data

@pytest.fixture
def context():
    return ExecutionContext(as_of_date="2026-05-08", run_id="test_run")

def test_liquidity_parsing_and_suspicious(context):
    node = LiquidityFilterNode()
    params = LiquidityFilterParams(min_trading_value_krw=2_000_000_000)
    
    # 1. 삼성전기 파싱 테스트 (K 단위)
    df = pd.DataFrame([{"code": "009150", "name": "삼성전기", "volume": "522.04K", "market_cap": 10_000_000_000_000}])
    ohlcv = {"009150": pd.DataFrame([{"date": "2026-05-08", "close": 914000, "volume": 522040}])}
    context.krx_client = MockKRX(ohlcv)
    res = node.run([df], params, context)
    assert res.iloc[0]["liquidity_status"] == "LIQUID"
    assert res.iloc[0]["liquidity_volume"] == 522040

    # 2. 현대차 파싱 테스트 (M 단위)
    df = pd.DataFrame([{"code": "005380", "name": "현대차", "volume": "5.00M", "market_cap": 50_000_000_000_000}])
    ohlcv = {"005380": pd.DataFrame([{"date": "2026-05-08", "close": 215000, "volume": 5000000}])}
    context.krx_client = MockKRX(ohlcv)
    res = node.run([df], params, context)
    assert res.iloc[0]["liquidity_status"] == "LIQUID"
    
    # 3. suspicious 감지 (대형주인데 거래량 극소)
    df = pd.DataFrame([{"code": "009150", "name": "삼성전기", "market_cap": 10_000_000_000_000}])
    ohlcv = {"009150": pd.DataFrame([{"date": "2026-05-08", "close": 914000, "volume": 134}])}
    context.krx_client = MockKRX(ohlcv)
    res = node.run([df], params, context)
    row = res.iloc[0]
    assert row["volume_suspicious"] == True
    assert row["liquidity_status"] == "LIQUIDITY_UNCERTAIN"
    assert "유동성 데이터 불확실" in row["liquidity_reason"]

def test_liquidity_nan_raw_trading_value(context):
    node = LiquidityFilterNode()
    params = LiquidityFilterParams()
    # raw_trading_value가 NaN이어도 close/volume이 있으면 정상 판정되어야 함
    df = pd.DataFrame([{"code": "005930", "name": "삼성전자", "market_cap": 500_000_000_000_000, "raw_trading_value": np.nan}])
    ohlcv = {"005930": pd.DataFrame([{"date": "2026-05-08", "close": 75000, "volume": 10_000_000}])}
    context.krx_client = MockKRX(ohlcv)
    res = node.run([df], params, context)
    assert res.iloc[0]["liquidity_status"] == "LIQUID"
    assert res.iloc[0]["calculated_trading_value"] == 75000 * 10_000_000

def test_watch_alert_hard_blocks(context):
    node = ScoreFilterNode()
    params = ScoreFilterParams()
    
    # 1. RS 저조 (rs_percentile < 50) 차단
    # 먼저 "수급/배열 최상"으로 alert 자격을 얻게 함
    df = pd.DataFrame([{
        "code": "RS_LOW", "name": "RS약세", "primary_bucket": "WATCHLIST",
        "rs_rating": 40, "rs_percentile": 45, "total_score": 100,
        "flow_total_score": 30, "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID"
    }])
    res = node.run([df], params, context)
    assert res.iloc[0]["watchlist_flag"] == False
    assert "RS 저조" in res.iloc[0]["watch_exclusion_reason"]

    # 2. 수급 취약 차단 (T2 리더십 아닌 경우)
    # 먼저 "양호한 셋업" (RS 60+)으로 alert 자격을 얻게 함
    df = pd.DataFrame([{
        "code": "FLOW_LOW", "name": "수급약세", "primary_bucket": "WATCHLIST",
        "rs_rating": 85, "rs_percentile": 85, "total_score": 100,
        "flow_total_score": 3, "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID",
        "breakout_distance_pct": 2.0
    }])
    res = node.run([df], params, context)
    assert res.iloc[0]["watchlist_flag"] == False
    assert "수급 극히 취약" in res.iloc[0]["watch_exclusion_reason"]

    # 3. TIER_2 + RS 80+ 예외 (수급이 약간 약해도 알림 유지 가능)
    df = pd.DataFrame([{
        "code": "T2_LEADER", "name": "T2리더", "primary_bucket": "TIER_2",
        "rs_rating": 90, "rs_percentile": 90, "total_score": 150,
        "flow_total_score": 4, "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID",
        "vcp_status": "VCP_STRICT", "breakout_status": "NEAR_BREAKOUT", 
        "breakout_distance_pct": 2.0, "box_breakout_pct": -2.0
    }])
    res = node.run([df], params, context)
    row = res.iloc[0]
    print(f"\nDEBUG: T2_LEADER -> bucket={row['primary_bucket']}, alert={row['watchlist_flag']}, reason={row['watchlist_flag_reason']}, exclusions={row['watch_exclusion_reason']}")
    # TIER_1 또는 TIER_2가 될 수 있음 (여기선 rs_percentile >= 80 이므로 alert 유지되어야 함)
    assert row["watchlist_flag"] == True
    assert row["watch_exclusion_reason"] == ""

    # 4. LIQUIDITY_UNCERTAIN: ACTION_ALERT 금지, DATA_REVIEW로 전환 (정책 v6.2 반영)
    # 사용자 정책: 초고RS + 유동성 불확실 → DATA_REVIEW (alert 켜짐, action 꺼짐)
    df = pd.DataFrame([{
        "code": "UNCERTAIN", "name": "불확실", "primary_bucket": "WATCHLIST",
        "rs_rating": 95, "rs_percentile": 95, "total_score": 150,
        "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUIDITY_UNCERTAIN"
    }])
    res = node.run([df], params, context)
    row = res.iloc[0]
    # ACTION_ALERT는 금지되어야 함
    assert bool(row["action_alert_flag"]) is False
    assert row["watch_alert_type"] != "ACTION_ALERT"
    # 그러나 DATA_REVIEW는 허용 (RS 95 + LIQUIDITY_UNCERTAIN)
    assert row["watch_alert_type"] in {"DATA_REVIEW", "RISK_WATCH", "NONE"}
    # 차단 사유에 LIQUIDITY 관련 차단 포함
    excl_str = str(row["watch_exclusion_reason"])
    assert "LIQUIDITY_UNCERTAIN_ACTION_BLOCK" in excl_str or "유동성" in excl_str

def test_nan_diagnostics_exclusion():
    from backend.analysis_summary import _top_nan_columns
    df = pd.DataFrame({
        "close": [1000, np.nan],
        "raw_trading_value": [np.nan, np.nan],
        "primary_bucket": ["TIER_1", "TIER_1"]
    })
    nans = _top_nan_columns(df)
    cols = [n["column"] for n in nans]
    assert "close" in cols
    assert "raw_trading_value" not in cols

def test_json_serialization_safety():
    from backend.analysis_summary import build_analysis_payload
    from engine.dag import ExecutionResult
    
    df = pd.DataFrame([{
        "code": "005930", "name": "삼성전자", 
        "liquidity_status": "LIQUID", "liquidity_trading_value": np.int64(1000000),
        "primary_bucket": "TIER_1", "total_score": 200, "volume_suspicious": False,
        "liquidity_data_warning": None, "watch_exclusion_reason": ""
    }])
    df.attrs["suspicious_liquidity_records"] = [
        {"symbol": "005930", "calculated_trading_value": np.float64(12345.67)}
    ]
    
    class MockLog:
        def __init__(self):
            self.node_id = "n1"; self.node_type = "liquidity_filter"
            self.input_count = 1; self.output_count = 1; self.latency_ms = 10.0
            self.cache_hit = False; self.data_missing_ratio = 0.0; self.nan_columns = []
            
    result = ExecutionResult(as_of_date="2026-05-08")
    result.success = True; result.outputs = {"n1": df}; result.node_logs = [MockLog()]
    
    payload = build_analysis_payload(result, {})
    import json
    # 에러 없이 직렬화되어야 함
    json_str = json.dumps(payload)
    assert "volume_suspicious_count" in json_str
