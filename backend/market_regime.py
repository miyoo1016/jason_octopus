from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.alphaforge_config import config_section


REGIME_KEYS = ("RISK_ON", "NEUTRAL", "RISK_OFF", "CRISIS")


def _normalize(scores: dict[str, float]) -> dict[str, int]:
    total = sum(max(0.0, float(v)) for v in scores.values()) or 1.0
    rounded = {k: int(round(max(0.0, float(scores.get(k, 0))) / total * 100)) for k in REGIME_KEYS}
    drift = 100 - sum(rounded.values())
    if drift:
        leader = max(rounded, key=rounded.get)
        rounded[leader] += drift
    return rounded


def _ranked_regimes(distribution: dict[str, int]) -> tuple[str, str]:
    ranked = sorted(REGIME_KEYS, key=lambda k: distribution.get(k, 0), reverse=True)
    return ranked[0], ranked[1]


def calculate_market_regime(
    *,
    vix: float | None = None,
    sp500_up: bool | None = None,
    kospi_up: bool | None = None,
    macro_status: str = "UNKNOWN",
    as_of_date: str = "",
    sources: list[str] | None = None,
) -> dict[str, Any]:
    cfg = config_section("market_regime")
    default = cfg.get("default_distribution", {"RISK_ON": 20, "NEUTRAL": 45, "RISK_OFF": 25, "CRISIS": 10})
    scores = {k: float(default.get(k, 0)) for k in REGIME_KEYS}
    missing = []

    if vix is None:
        missing.append("VIX")
    else:
        if vix <= float(cfg.get("vix_risk_on", 18)):
            scores["RISK_ON"] += 25
            scores["NEUTRAL"] += 5
        elif vix >= float(cfg.get("vix_crisis", 35)):
            scores["CRISIS"] += 45
            scores["RISK_OFF"] += 20
        elif vix >= float(cfg.get("vix_risk_off", 25)):
            scores["RISK_OFF"] += 35
            scores["CRISIS"] += 10
        else:
            scores["NEUTRAL"] += 20

    if sp500_up is None:
        missing.append("S&P500 trend")
    elif sp500_up:
        scores["RISK_ON"] += 20
        scores["NEUTRAL"] += 5
    else:
        scores["RISK_OFF"] += 25
        scores["CRISIS"] += 5

    if kospi_up is None:
        missing.append("KOSPI trend")
    elif kospi_up:
        scores["RISK_ON"] += 15
    else:
        scores["RISK_OFF"] += 15

    distribution = _normalize(scores)
    dominant, secondary = _ranked_regimes(distribution)
    if missing:
        data_status = "일부 결측"
    elif macro_status in {"Cached", "Default"}:
        data_status = "추정"
    else:
        data_status = "지연"

    return {
        "regime_probabilities": distribution,
        "risk_on_prob": distribution["RISK_ON"],
        "neutral_prob": distribution["NEUTRAL"],
        "risk_off_prob": distribution["RISK_OFF"],
        "crisis_prob": distribution["CRISIS"],
        "dominant_regime": dominant,
        "secondary_regime": secondary,
        "regime_as_of": as_of_date or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "regime_data_sources": sources or ["yfinance:^VIX", "yfinance:^GSPC", "KRX:069500"],
        "regime_data_status": data_status,
        "regime_missing_inputs": missing,
    }
