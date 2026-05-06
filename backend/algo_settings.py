import yaml
from pathlib import Path
from typing import Dict, Any

_SETTINGS_FILE = Path(__file__).parent.parent / "settings.yaml"

class AlgoSettings:
    """
    settings.yaml 파일에서 트레이딩 알고리즘 설정값을 로드하는 싱글턴.
    """
    def __init__(self, filepath: Path = _SETTINGS_FILE):
        self._filepath = filepath
        self._data: Dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        if self._filepath.exists():
            with open(self._filepath, "r", encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}
        else:
            self._data = {}

    @property
    def vcp_min_score(self) -> int:
        return self._data.get("vcp_min_score", 70)

    @property
    def vcp_lookback_days(self) -> int:
        return self._data.get("vcp_lookback_days", 120)

    @property
    def vcp_pivot_window(self) -> int:
        return self._data.get("vcp_pivot_window", 5)

    @property
    def vcp_max_depth_pct(self) -> float:
        return float(self._data.get("vcp_max_depth_pct", 50))

    @property
    def breakout_vol_A(self) -> float:
        return float(self._data.get("breakout_vol_A", 2.0))

    @property
    def breakout_vol_B(self) -> float:
        return float(self._data.get("breakout_vol_B", 1.5))

    @property
    def breakout_vol_C(self) -> float:
        return float(self._data.get("breakout_vol_C", 1.0))

    @property
    def flow_days_short(self) -> int:
        return self._data.get("flow_days_short", 5)

    @property
    def flow_days_mid(self) -> int:
        return self._data.get("flow_days_mid", 60)

    @property
    def flow_days_long(self) -> int:
        return self._data.get("flow_days_long", 120)

    @property
    def rs_min_rating(self) -> int:
        return self._data.get("rs_min_rating", 80)

    @property
    def sector_cluster_warn(self) -> int:
        return self._data.get("sector_cluster_warn", 3)

    @property
    def vix_penalty_start(self) -> float:
        return float(self._data.get("vix_penalty_start", 22))

    @property
    def vix_hard_block(self) -> float:
        return float(self._data.get("vix_hard_block", 30))

    @property
    def vix_relative_threshold(self) -> float:
        return float(self._data.get("vix_relative_threshold", 1.3))

    @property
    def us10y_warn(self) -> float:
        return float(self._data.get("us10y_warn", 4.6))

    @property
    def dxy_warn(self) -> float:
        return float(self._data.get("dxy_warn", 104))

    @property
    def weights(self) -> Dict[str, float]:
        w = self._data.get("weights", {})
        return {
            "vcp_score": float(w.get("vcp_score", 0.25)),
            "breakout_grade": float(w.get("breakout_grade", 0.20)),
            "rs_rating": float(w.get("rs_rating", 0.20)),
            "flow_quality": float(w.get("flow_quality", 0.20)),
            "sector_check": float(w.get("sector_check", 0.10)),
            "macro_display": float(w.get("macro_display", 0.05)),
        }


# 모듈 단위 싱글턴
algo_settings = AlgoSettings()
