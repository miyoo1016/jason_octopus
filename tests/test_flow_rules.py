import pandas as pd

from data.krx import KRXClient
from engine.node_base import ExecutionContext
from nodes.foreign_flow import ForeignFlowNode, ForeignFlowParams
from nodes.institution_flow import InstitutionFlowNode, InstitutionFlowParams
from nodes.score_filter import ScoreFilterNode, ScoreFilterParams


def test_krx_foreign_flow_uses_same_day_range_and_preserves_missing(monkeypatch, tmp_path):
    calls = []

    def fake_net_purchases(start, end, market, investor):
        calls.append((start, end, market, investor))
        if market == "KOSPI":
            return pd.DataFrame(
                {"순매수수량": [123]},
                index=pd.Index(["001440"], name="티커"),
            )
        return pd.DataFrame()

    monkeypatch.setattr(
        "data.krx.pykrx_stock.get_market_net_purchases_of_equities_by_ticker",
        fake_net_purchases,
    )

    universe = pd.DataFrame(
        [
            {"code": "001440", "name": "대한전선", "market": "KOSPI", "close": 1000, "volume": 10},
            {"code": "005930", "name": "삼성전자", "market": "KOSPI", "close": 80000, "volume": 10},
        ]
    )

    result = KRXClient(tmp_path).get_foreign_flow(universe, "2026-05-07", n_days=5, force_refresh=True)

    assert calls[0] == ("20260507", "20260507", "KOSPI", "외국인")
    assert calls[1] == ("20260507", "20260507", "KOSDAQ", "외국인")
    assert result.loc[result["code"] == "001440", "foreign_net_buy"].iloc[0] == 123
    assert pd.isna(result.loc[result["code"] == "005930", "foreign_net_buy"].iloc[0])


def test_foreign_flow_score_is_missing_when_daily_flow_is_missing():
    class FakeClient:
        def get_foreign_flow(self, df, as_of_date, n_days=5):
            result = df.copy()
            result["foreign_net_buy"] = pd.Series([pd.NA], dtype="Int64")
            result.attrs["foreign_flow_hist"] = {
                "005930": [(pd.Timestamp("2026-05-07"), 1_000_000, 0)]
            }
            return result

    df = pd.DataFrame(
        [{"code": "005930", "name": "삼성전자", "market": "KOSPI", "close": 80000, "volume": 10}]
    )
    context = ExecutionContext(as_of_date="2026-05-07", run_id="test", krx_client=FakeClient())

    result = ForeignFlowNode().run([df], ForeignFlowParams(n_days=5), context)

    assert result.loc[0, "flow_score"] == 10
    assert result.loc[0, "foreign_flow_flag"] == "ESTIMATED"


def test_institution_flow_uses_hist_and_scores_without_name_error():
    class FakeClient:
        def get_institution_flow(self, df, as_of_date, n_days=5):
            result = df.copy()
            result["institution_net_buy"] = pd.Series([pd.NA], dtype="Int64")
            result.attrs["institution_flow_hist"] = {
                "005930": [
                    (pd.Timestamp("2026-05-07"), 1_000_000, 700_000),
                    (pd.Timestamp("2026-05-06"), 800_000, 600_000),
                    (pd.Timestamp("2026-05-04"), 700_000, 500_000),
                ]
            }
            return result

    df = pd.DataFrame(
        [{"code": "005930", "name": "삼성전자", "market": "KOSPI", "close": 80000, "volume": 10}]
    )
    context = ExecutionContext(as_of_date="2026-05-07", run_id="test", krx_client=FakeClient())

    result = InstitutionFlowNode().run([df], InstitutionFlowParams(n_days=5), context)

    assert result.loc[0, "institution_flow_score"] > 0
    assert not pd.isna(result.loc[0, "institution_net_buy"])


def test_flow_caps_foreign_sell_institution_buy_case():
    df = pd.DataFrame(
        [
            {
                "code": "001440",
                "name": "대한전선",
                "market": "KOSPI",
                "close": 1000,
                "volume": 10,
                "vcp_score": 0,
                "flow_score": 30,
                "institution_flow_score": 30,
                "foreign_net_buy": -2_210_000,
                "institution_net_buy": 1_250_000,
            }
        ]
    )
    context = ExecutionContext(as_of_date="2026-05-07", run_id="test")

    result = ScoreFilterNode().run([df], ScoreFilterParams(), context)

    assert result.loc[0, "flow_total_score"] == 15
    assert result.loc[0, "total_score"] == 15


def test_flow_caps_both_selling_to_five_points():
    df = pd.DataFrame(
        [
            {
                "code": "000000",
                "name": "테스트",
                "market": "KOSPI",
                "close": 1000,
                "volume": 10,
                "vcp_score": 0,
                "flow_score": 30,
                "institution_flow_score": 30,
                "foreign_net_buy": -50_000,
                "institution_net_buy": -10_000,
            }
        ]
    )
    context = ExecutionContext(as_of_date="2026-05-07", run_id="test")

    result = ScoreFilterNode().run([df], ScoreFilterParams(), context)

    assert result.loc[0, "flow_total_score"] == 5
    assert result.loc[0, "total_score"] == 5
