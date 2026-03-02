"""
Tests for config.py — ConfigManager
"""
import json
import os
import tempfile
import threading
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import ConfigManager, _deep_merge, DEFAULT_CONFIG


class TestDeepMerge(unittest.TestCase):

    def test_flat_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 99, "c": 3}
        result = _deep_merge(base, override)
        self.assertEqual(result, {"a": 1, "b": 99, "c": 3})

    def test_nested_merge(self):
        base = {"ema": {"fast": 20, "mid": 50}}
        override = {"ema": {"fast": 10}}
        result = _deep_merge(base, override)
        self.assertEqual(result["ema"], {"fast": 10, "mid": 50})

    def test_nested_does_not_mutate_base(self):
        base = {"ema": {"fast": 20}}
        override = {"ema": {"fast": 99}}
        _deep_merge(base, override)
        self.assertEqual(base["ema"]["fast"], 20)

    def test_list_override_not_merged(self):
        base = {"symbols": ["AAPL", "TSLA"]}
        override = {"symbols": ["GOOG"]}
        result = _deep_merge(base, override)
        self.assertEqual(result["symbols"], ["GOOG"])

    def test_empty_override_returns_base(self):
        base = {"a": 1}
        result = _deep_merge(base, {})
        self.assertEqual(result, {"a": 1})

    def test_empty_base_returns_override(self):
        result = _deep_merge({}, {"a": 1})
        self.assertEqual(result, {"a": 1})


class TestConfigManager(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp.close()
        os.unlink(self._tmp.name)   # remove so ConfigManager creates it fresh
        self.cfg = ConfigManager(path=self._tmp.name)

    def tearDown(self):
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)
        tmp = self._tmp.name + ".tmp"
        if os.path.exists(tmp):
            os.unlink(tmp)

    # ── Defaults ──────────────────────────────────────────────────────────────

    def test_default_symbols(self):
        self.assertEqual(self.cfg.symbols, ["AAPL", "TSLA", "NVDA", "SPY"])

    def test_default_ema_periods(self):
        self.assertEqual(self.cfg.ema_fast, 20)
        self.assertEqual(self.cfg.ema_mid, 50)
        self.assertEqual(self.cfg.ema_slow, 200)

    def test_default_atr(self):
        self.assertEqual(self.cfg.atr_period, 14)
        self.assertAlmostEqual(self.cfg.atr_multiplier, 0.5)

    def test_default_cooldown(self):
        self.assertEqual(self.cfg.alert_cooldown_minutes, 15)

    def test_default_poll_interval(self):
        self.assertEqual(self.cfg.poll_interval_seconds, 60)

    def test_default_discord_disabled(self):
        self.assertFalse(self.cfg.discord_enabled)
        self.assertEqual(self.cfg.discord_webhook_url, "")

    def test_default_ui(self):
        self.assertEqual(self.cfg.ui_refresh_interval_ms, 2000)
        self.assertIn("x", self.cfg.ui_window_geometry)

    # ── Persistence ───────────────────────────────────────────────────────────

    def test_config_file_created_on_init(self):
        self.assertTrue(os.path.exists(self._tmp.name))

    def test_config_persists_across_reload(self):
        self.cfg.symbols = ["MSFT", "AMZN"]
        # Reload from same file
        cfg2 = ConfigManager(path=self._tmp.name)
        self.assertEqual(cfg2.symbols, ["MSFT", "AMZN"])

    def test_corrupt_json_falls_back_to_defaults(self):
        with open(self._tmp.name, "w") as f:
            f.write("{ NOT VALID JSON }")
        cfg = ConfigManager(path=self._tmp.name)
        self.assertEqual(cfg.symbols, DEFAULT_CONFIG["symbols"])

    def test_partial_json_fills_missing_keys(self):
        with open(self._tmp.name, "w") as f:
            json.dump({"symbols": ["GOOG"]}, f)
        cfg = ConfigManager(path=self._tmp.name)
        self.assertEqual(cfg.symbols, ["GOOG"])
        # Missing keys should be filled from defaults
        self.assertEqual(cfg.ema_fast, 20)

    # ── get / set ─────────────────────────────────────────────────────────────

    def test_get_nested(self):
        val = self.cfg.get("ema_periods", "fast")
        self.assertEqual(val, 20)

    def test_get_missing_returns_default(self):
        val = self.cfg.get("nonexistent_key", default=42)
        self.assertEqual(val, 42)

    def test_set_nested(self):
        self.cfg.set("ema_periods", "fast", value=10)
        self.assertEqual(self.cfg.ema_fast, 10)

    def test_set_creates_intermediate_dicts(self):
        self.cfg.set("new_section", "sub_key", value="hello")
        self.assertEqual(self.cfg.get("new_section", "sub_key"), "hello")

    def test_update_section_merges(self):
        self.cfg.update_section("discord", {"enabled": True, "webhook_url": "https://example.com"})
        self.assertTrue(self.cfg.discord_enabled)
        self.assertEqual(self.cfg.discord_webhook_url, "https://example.com")

    def test_update_section_does_not_overwrite_other_keys(self):
        self.cfg.update_section("discord", {"enabled": True})
        # webhook_url should still be default ""
        self.assertEqual(self.cfg.discord_webhook_url, "")

    # ── Symbol setter ─────────────────────────────────────────────────────────

    def test_symbols_setter(self):
        self.cfg.symbols = ["FB", "NFLX"]
        self.assertEqual(self.cfg.symbols, ["FB", "NFLX"])

    def test_symbols_returns_copy(self):
        s1 = self.cfg.symbols
        s1.append("MUTATED")
        self.assertNotIn("MUTATED", self.cfg.symbols)

    def test_snapshot_is_deep_copy(self):
        snap = self.cfg.snapshot()
        snap["symbols"].append("MUTATED")
        self.assertNotIn("MUTATED", self.cfg.symbols)

    # ── Thread safety ─────────────────────────────────────────────────────────

    def test_concurrent_writes_no_exception(self):
        errors = []

        def writer(val):
            try:
                self.cfg.symbols = [f"SYM{val}"]
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # Final value must be a valid list
        self.assertIsInstance(self.cfg.symbols, list)


if __name__ == "__main__":
    unittest.main()
