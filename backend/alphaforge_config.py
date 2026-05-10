from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "alphaforge.config.json"


DEFAULT_CONFIG: dict[str, Any] = {
    "version": "1.2",
    "market_regime": {
        "vix_risk_on": 18,
        "vix_risk_off": 25,
        "vix_crisis": 35,
        "default_distribution": {"RISK_ON": 20, "NEUTRAL": 45, "RISK_OFF": 25, "CRISIS": 10},
    },
    "risk_gate": {
        "risk_off_hold_classes": ["TIER_2"],
        "crisis_block_classes": ["TIER_1", "TIER_2"],
        "hard_fail_liquidity_statuses": ["ILLIQUID"],
        "hold_data_statuses": ["DATA_MISSING", "LIQUIDITY_UNKNOWN", "LIQUIDITY_UNCERTAIN"],
    },
    "watch_alert": {
        "near_box_pct": 3.0,
        "setup_box_pct": 7.0,
        "rs_leadership": 80,
        "rs_near_breakout": 70,
        "flow_strong": 25,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def load_alphaforge_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_CONFIG
    return _deep_merge(DEFAULT_CONFIG, loaded)


def config_section(name: str) -> dict[str, Any]:
    section = load_alphaforge_config().get(name, {})
    return section if isinstance(section, dict) else {}
