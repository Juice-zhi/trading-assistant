"""
main.py — Entry point for the Trading Assistant.

Architecture:
  - Main thread: tkinter event loop (UI)
  - Scanner thread (daemon): 60s poll loop, fetches OHLCV, runs state machine
  - Communication: queue.Queue (scanner → UI)
  - Market hours: 9:30–11:30 AM ET on weekdays only
"""
import logging
import queue
import threading
import time
import tkinter as tk
from datetime import datetime, time as dt_time
from typing import Optional

import pytz

from config import ConfigManager
from data_fetcher import fetch_ohlcv
from notifier import DiscordNotifier
from scanner import Alert, StockScanner, compute_indicators
from ui.main_window import MainWindow
from ui.settings_dialog import SettingsDialog

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# ── Market hours ──────────────────────────────────────────────────────────────
ET = pytz.timezone("America/New_York")
MARKET_OPEN  = dt_time(9, 30)
MARKET_CLOSE = dt_time(16, 0)


def is_market_open() -> bool:
    """Return True if current ET time is within regular trading hours on a weekday."""
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    current_time = now_et.time()
    return MARKET_OPEN <= current_time <= MARKET_CLOSE


# ── Scanner thread ─────────────────────────────────────────────────────────────

class ScannerThread(threading.Thread):
    """
    Background daemon thread that polls market data and runs the state machine.

    Alerts are pushed to alert_queue for the UI.
    A DiscordNotifier is instantiated each scan cycle (picks up config changes).
    """

    def __init__(self, config: ConfigManager, alert_queue: queue.Queue):
        super().__init__(daemon=True, name="ScannerThread")
        self._config = config
        self._alert_queue = alert_queue
        self._stop_event = threading.Event()
        self._scanner_lock = threading.Lock()
        self._scanner: StockScanner = self._build_scanner()

    def _build_scanner(self) -> StockScanner:
        return StockScanner(
            alert_queue=self._alert_queue,
            ema_fast=self._config.ema_fast,
            ema_mid=self._config.ema_mid,
            ema_slow=self._config.ema_slow,
            atr_period=self._config.atr_period,
            atr_multiplier=self._config.atr_multiplier,
            cooldown_minutes=self._config.alert_cooldown_minutes,
            trend_confirm_bars=self._config.trend_confirm_bars,
            trend_exit_bars=self._config.trend_exit_bars,
            consolidation_min_votes=self._config.consolidation_min_votes,
            consolidation_bb_period=self._config.consolidation_bb_period,
            consolidation_bb_threshold=self._config.consolidation_bb_threshold,
            consolidation_ema_gap_atr=self._config.consolidation_ema_gap_atr,
            consolidation_range_bars=self._config.consolidation_range_bars,
            consolidation_range_threshold=self._config.consolidation_range_threshold,
        )

    def rebuild_scanner(self) -> None:
        """Rebuild scanner with updated config (called after settings save)."""
        with self._scanner_lock:
            self._scanner = self._build_scanner()

    def remove_symbol(self, symbol: str) -> None:
        with self._scanner_lock:
            self._scanner.remove_symbol(symbol)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info("Scanner thread started")

        # Always do an initial scan on startup to populate the UI immediately
        logger.info("Initial scan (market hours ignored)...")
        self._scan_cycle()

        while not self._stop_event.is_set():
            market_open = is_market_open()
            self._alert_queue.put({"type": "market_status", "open": market_open})

            if market_open:
                self._scan_cycle()
            else:
                logger.debug("Outside market hours — sleeping 30s")
                self._stop_event.wait(30)
                continue

            # Interruptible sleep for poll_interval
            interval = self._config.poll_interval_seconds
            end_time = time.monotonic() + interval
            while time.monotonic() < end_time and not self._stop_event.is_set():
                self._stop_event.wait(1)

        logger.info("Scanner thread stopped")

    def _scan_cycle(self) -> None:
        symbols = list(self._config.symbols)   # snapshot before iteration
        total = len(symbols)
        notifier = DiscordNotifier(
            webhook_url=self._config.discord_webhook_url,
            enabled=self._config.discord_enabled,
        )

        # Use a local capture queue so Discord alerts don't require queue surgery
        capture_queue: queue.Queue = queue.Queue()

        with self._scanner_lock:
            # Temporarily redirect scanner output to capture queue
            original_queue = self._scanner._queue
            self._scanner._queue = capture_queue

            try:
                for idx, symbol in enumerate(symbols, start=1):
                    if self._stop_event.is_set():
                        break
                    self._alert_queue.put({
                        "type": "scan_progress",
                        "symbol": symbol,
                        "done": idx,
                        "total": total,
                    })
                    df = fetch_ohlcv(symbol)
                    if df is None:
                        logger.warning("No data for %s, skipping", symbol)
                        continue
                    self._scanner.process(symbol, df)
                    self._push_indicator_update(symbol, df)
            finally:
                # Always restore the original queue, even if an exception occurs
                self._scanner._queue = original_queue

        # Forward captured alerts: UI queue + Discord (outside the lock)
        while not capture_queue.empty():
            alert: Alert = capture_queue.get_nowait()
            self._alert_queue.put(alert)
            notifier.send_alert(alert)

        self._alert_queue.put({
            "type": "scan_complete",
            "total": total,
            "timestamp": datetime.now(ET).strftime("%H:%M:%S"),
        })

    def _push_indicator_update(self, symbol: str, df) -> None:
        """Push a raw indicator snapshot to the UI queue (no alert, no Discord)."""
        try:
            out = compute_indicators(
                df,
                ema_fast=self._config.ema_fast,
                ema_mid=self._config.ema_mid,
                ema_slow=self._config.ema_slow,
                atr_period=self._config.atr_period,
            )
            last = out.iloc[-1]
            atr = float(last["atr"])
            if atr <= 0:
                return
            snap = self._scanner.get_snapshot(symbol)
            self._alert_queue.put({
                "type": "indicator_update",
                "symbol": symbol,
                "price": float(last["Close"]),
                "ema_fast": float(last["ema_fast"]),
                "ema_mid": float(last["ema_mid"]),
                "ema_slow": float(last["ema_slow"]),
                "atr": atr,
                "dist_atr": abs(float(last["Close"]) - float(last["ema_fast"])) / atr,
                "signal_state": snap[0].name if snap else "NO_TREND",
                "direction": snap[1].value if snap else "none",
            })
        except Exception as exc:
            logger.debug("indicator_update failed for %s: %s", symbol, exc)


# ── Application ───────────────────────────────────────────────────────────────

def build_app():
    config = ConfigManager()
    alert_queue: queue.Queue = queue.Queue()

    root = tk.Tk()
    root.geometry(config.ui_window_geometry)

    scanner_thread = ScannerThread(config=config, alert_queue=alert_queue)

    def on_settings():
        def after_save():
            scanner_thread.rebuild_scanner()
            window.refresh_symbols()
        SettingsDialog(root, config=config, on_save=after_save)

    def on_add_symbol(symbol: str) -> None:
        symbols = config.symbols
        if symbol not in symbols:
            symbols.append(symbol)
            config.symbols = symbols

    def on_remove_symbol(symbol: str) -> None:
        symbols = config.symbols
        if symbol in symbols:
            symbols.remove(symbol)
            config.symbols = symbols
        scanner_thread.remove_symbol(symbol)

    window = MainWindow(
        root=root,
        alert_queue=alert_queue,
        config=config,
        on_settings=on_settings,
        on_add_symbol=on_add_symbol,
        on_remove_symbol=on_remove_symbol,
        refresh_interval_ms=config.ui_refresh_interval_ms,
    )

    return root, scanner_thread, config, window


def main():
    root, scanner_thread, config, window = build_app()

    logger.info("Starting Trading Assistant")
    scanner_thread.start()

    def on_close():
        logger.info("Shutting down...")
        scanner_thread.stop()
        config.update_section("ui", {"window_geometry": root.winfo_geometry()})
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        on_close()


if __name__ == "__main__":
    main()
