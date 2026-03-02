"""
ConfigManager: JSON-backed configuration with deep-merge and typed properties.
"""
import json
import logging
import os
import copy
import threading
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "symbols": ["AAPL", "TSLA", "NVDA", "SPY"],
    "ema_periods": {"fast": 20, "mid": 50, "slow": 200},
    "atr_period": 14,
    "atr_multiplier": 0.5,
    "consolidation_slope_threshold": 0.001,  # EMA50 slope filter; 0 to disable
    "consolidation_lookback": 10,             # bars to measure EMA50 slope
    "alert_cooldown_minutes": 15,
    "poll_interval_seconds": 60,
    "discord": {"enabled": False, "webhook_url": ""},
    "ui": {"refresh_interval_ms": 2000, "window_geometry": "1200x600"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ConfigManager:
    """Thread-safe JSON-backed configuration manager."""

    def __init__(self, path: str = CONFIG_PATH):
        self._path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                self._data = _deep_merge(DEFAULT_CONFIG, stored)
            except (json.JSONDecodeError, OSError):
                self._data = copy.deepcopy(DEFAULT_CONFIG)
        else:
            self._data = copy.deepcopy(DEFAULT_CONFIG)
        self._save()

    def _save(self) -> None:
        try:
            # Write to temp file then rename for atomic update
            tmp_path = self._path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            logger.warning("Config save failed (%s): %s", self._path, exc)

    def get(self, *keys: str, default: Any = None) -> Any:
        """Get a nested value by key path."""
        with self._lock:
            node = self._data
            for k in keys:
                if not isinstance(node, dict) or k not in node:
                    return default
                node = node[k]
            return node

    def set(self, *keys: str, value: Any) -> None:
        """Set a nested value by key path and persist."""
        with self._lock:
            node = self._data
            for k in keys[:-1]:
                if k not in node or not isinstance(node[k], dict):
                    node[k] = {}
                node = node[k]
            node[keys[-1]] = value
            self._save()

    def update_section(self, section: str, values: dict) -> None:
        """Merge a dict into a top-level section and persist."""
        with self._lock:
            if section not in self._data or not isinstance(self._data[section], dict):
                self._data[section] = {}
            self._data[section] = _deep_merge(self._data[section], values)
            self._save()

    def snapshot(self) -> Dict[str, Any]:
        """Return a deep copy of the current config."""
        with self._lock:
            return copy.deepcopy(self._data)

    # ── Typed convenience properties ─────────────────────────────────────────

    @property
    def symbols(self) -> List[str]:
        with self._lock:
            return list(self._data.get("symbols", []))

    @symbols.setter
    def symbols(self, value: List[str]) -> None:
        self.set("symbols", value=list(value))

    @property
    def ema_fast(self) -> int:
        return int(self.get("ema_periods", "fast", default=20))

    @property
    def ema_mid(self) -> int:
        return int(self.get("ema_periods", "mid", default=50))

    @property
    def ema_slow(self) -> int:
        return int(self.get("ema_periods", "slow", default=200))

    @property
    def atr_period(self) -> int:
        return int(self.get("atr_period", default=14))

    @property
    def atr_multiplier(self) -> float:
        return float(self.get("atr_multiplier", default=0.5))

    @property
    def consolidation_slope_threshold(self) -> float:
        return float(self.get("consolidation_slope_threshold", default=0.001))

    @property
    def consolidation_lookback(self) -> int:
        return int(self.get("consolidation_lookback", default=10))

    @property
    def alert_cooldown_minutes(self) -> int:
        return int(self.get("alert_cooldown_minutes", default=15))

    @property
    def poll_interval_seconds(self) -> int:
        return int(self.get("poll_interval_seconds", default=60))

    @property
    def discord_enabled(self) -> bool:
        return bool(self.get("discord", "enabled", default=False))

    @property
    def discord_webhook_url(self) -> str:
        return str(self.get("discord", "webhook_url", default=""))

    @property
    def ui_refresh_interval_ms(self) -> int:
        return int(self.get("ui", "refresh_interval_ms", default=2000))

    @property
    def ui_window_geometry(self) -> str:
        return str(self.get("ui", "window_geometry", default="1200x600"))
