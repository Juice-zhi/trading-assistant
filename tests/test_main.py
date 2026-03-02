"""
Tests for main.py — is_market_open(), ScannerThread lifecycle
"""
import os
import queue
import sys
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import ScannerThread, is_market_open

ET = pytz.timezone("America/New_York")


class TestIsMarketOpen(unittest.TestCase):

    def _mock_now(self, weekday, hour, minute):
        """Return a mock ET datetime with given weekday (0=Mon) and time."""
        dt = MagicMock()
        dt.weekday.return_value = weekday
        dt.time.return_value = datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time()
        return dt

    @patch("main.datetime")
    def test_open_at_930(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(weekday=0, hour=9, minute=30)
        self.assertTrue(is_market_open())

    @patch("main.datetime")
    def test_open_at_1100(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(weekday=2, hour=11, minute=0)
        self.assertTrue(is_market_open())

    @patch("main.datetime")
    def test_open_at_1130(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(weekday=4, hour=11, minute=30)
        self.assertTrue(is_market_open())

    @patch("main.datetime")
    def test_closed_before_930(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(weekday=1, hour=9, minute=29)
        self.assertFalse(is_market_open())

    @patch("main.datetime")
    def test_closed_after_1130(self, mock_dt):
        # Market runs until 16:00 now; 11:31 is still open
        mock_dt.now.return_value = self._mock_now(weekday=1, hour=11, minute=31)
        self.assertTrue(is_market_open())

    @patch("main.datetime")
    def test_closed_after_1600(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(weekday=1, hour=16, minute=1)
        self.assertFalse(is_market_open())

    @patch("main.datetime")
    def test_closed_on_saturday(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(weekday=5, hour=10, minute=0)
        self.assertFalse(is_market_open())

    @patch("main.datetime")
    def test_closed_on_sunday(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(weekday=6, hour=10, minute=0)
        self.assertFalse(is_market_open())

    @patch("main.datetime")
    def test_open_monday(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(weekday=0, hour=10, minute=0)
        self.assertTrue(is_market_open())

    @patch("main.datetime")
    def test_open_friday(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(weekday=4, hour=10, minute=0)
        self.assertTrue(is_market_open())

    @patch("main.datetime")
    def test_closed_midnight(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(weekday=1, hour=0, minute=0)
        self.assertFalse(is_market_open())


class TestScannerThreadLifecycle(unittest.TestCase):

    def _make_thread(self):
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        os.unlink(tmp.name)
        from config import ConfigManager
        cfg = ConfigManager(path=tmp.name)
        cfg.set("poll_interval_seconds", value=1)
        q = queue.Queue()
        thread = ScannerThread(config=cfg, alert_queue=q)
        self._cfg_path = tmp.name
        return thread, q, cfg

    def tearDown(self):
        if hasattr(self, "_cfg_path") and os.path.exists(self._cfg_path):
            os.unlink(self._cfg_path)

    def test_thread_is_daemon(self):
        thread, q, _ = self._make_thread()
        self.assertTrue(thread.daemon)

    def test_thread_starts_and_stops(self):
        thread, q, _ = self._make_thread()
        thread.start()
        self.assertTrue(thread.is_alive())
        thread.stop()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_stop_before_start_no_error(self):
        thread, q, _ = self._make_thread()
        thread.stop()   # Should not raise

    def test_market_status_pushed_to_queue(self):
        """Scanner thread always pushes market_status regardless of hours."""
        thread, q, cfg = self._make_thread()

        # Patch both is_market_open AND fetch_ohlcv so the initial scan completes fast
        with patch("main.is_market_open", return_value=False), \
             patch("main.fetch_ohlcv", return_value=None):
            thread.start()
            time.sleep(0.5)
            thread.stop()
            thread.join(timeout=5)

        messages = []
        while not q.empty():
            messages.append(q.get_nowait())

        types = [m.get("type") for m in messages if isinstance(m, dict)]
        self.assertIn("market_status", types)

    def test_rebuild_scanner_no_error(self):
        thread, q, _ = self._make_thread()
        thread.rebuild_scanner()   # Should not raise (not started yet)

    def test_remove_symbol_no_error(self):
        thread, q, _ = self._make_thread()
        thread.remove_symbol("AAPL")   # Should not raise


class TestScannerThreadScanCycle(unittest.TestCase):
    """Test _scan_cycle behavior with mocked fetch_ohlcv."""

    def _make_thread(self):
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        os.unlink(tmp.name)
        from config import ConfigManager
        cfg = ConfigManager(path=tmp.name)
        cfg.symbols = ["AAPL", "TSLA"]
        q = queue.Queue()
        thread = ScannerThread(config=cfg, alert_queue=q)
        self._cfg_path = tmp.name
        return thread, q, cfg

    def tearDown(self):
        if hasattr(self, "_cfg_path") and os.path.exists(self._cfg_path):
            os.unlink(self._cfg_path)

    @patch("main.fetch_ohlcv", return_value=None)
    def test_scan_cycle_handles_none_data(self, mock_fetch):
        thread, q, _ = self._make_thread()
        thread._scan_cycle()   # Should not raise
        # scan_status messages should be in queue
        messages = [q.get_nowait() for _ in range(q.qsize())]
        types = [m.get("type") for m in messages if isinstance(m, dict)]
        self.assertIn("scan_status", types)

    @patch("main.fetch_ohlcv", return_value=None)
    def test_scan_cycle_processes_all_symbols(self, mock_fetch):
        thread, q, cfg = self._make_thread()
        cfg.symbols = ["AAPL", "TSLA", "NVDA"]
        thread._scan_cycle()
        # Should have tried to fetch all 3 symbols
        self.assertEqual(mock_fetch.call_count, 3)

    @patch("main.fetch_ohlcv", return_value=None)
    def test_scanner_queue_restored_after_cycle(self, mock_fetch):
        """scanner._queue must be restored to alert_queue after _scan_cycle."""
        thread, q, _ = self._make_thread()
        original_queue = thread._scanner._queue
        thread._scan_cycle()
        self.assertIs(thread._scanner._queue, original_queue)

    @patch("main.fetch_ohlcv")
    def test_scan_cycle_with_real_data(self, mock_fetch):
        """Verify alerts from scanner are forwarded to alert_queue."""
        import pandas as pd
        n = 300
        prices = pd.Series([100.0 + i * 0.1 for i in range(n)])
        df = pd.DataFrame({
            "Open": prices, "High": prices * 1.005,
            "Low": prices * 0.995, "Close": prices,
            "Volume": [1e6] * n,
        })
        mock_fetch.return_value = df

        thread, q, cfg = self._make_thread()
        cfg.symbols = ["TEST"]
        thread._scan_cycle()

        # Drain the queue
        items = []
        while not q.empty():
            items.append(q.get_nowait())

        # At minimum, scan_status was emitted
        types = [i.get("type") for i in items if isinstance(i, dict)]
        self.assertIn("scan_status", types)


if __name__ == "__main__":
    unittest.main()
