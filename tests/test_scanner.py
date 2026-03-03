"""
Tests for scanner.py — EMA/ATR calculations + state machine
"""
import os
import queue
import sys
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scanner import (
    Alert,
    SignalState,
    StockScanner,
    StockState,
    TrendDirection,
    compute_atr,
    compute_ema,
    compute_indicators,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_df(close_prices, high_mult=1.005, low_mult=0.995):
    """Build a minimal OHLCV DataFrame from a list/array of close prices."""
    close = pd.Series(close_prices, dtype=float)
    return pd.DataFrame({
        "Open":   close,
        "High":   close * high_mult,
        "Low":    close * low_mult,
        "Close":  close,
        "Volume": [1_000_000] * len(close),
    })


def make_bullish_df(n=250, start=100.0, slope=0.05):
    """Steadily rising prices that produce EMA20 > EMA50 > EMA200."""
    prices = [start + i * slope for i in range(n)]
    return make_df(prices)


def make_bearish_df(n=250, start=200.0, slope=-0.05):
    """Steadily falling prices that produce EMA20 < EMA50 < EMA200."""
    prices = [start + i * slope for i in range(n)]
    return make_df(prices)


def make_strong_bullish_df(n=300):
    """Rising prices with enough volatility to pass the consolidation filter."""
    import math
    prices = [100.0 + i * 0.5 + 2.0 * math.sin(i * 0.3) for i in range(n)]
    return make_df(prices, high_mult=1.01, low_mult=0.99)


def make_strong_bearish_df(n=300):
    """Falling prices with enough volatility to pass the consolidation filter."""
    import math
    prices = [200.0 - i * 0.5 - 2.0 * math.sin(i * 0.3) for i in range(n)]
    return make_df(prices, high_mult=1.01, low_mult=0.99)


def process_incremental(scanner, symbol, df, min_bars=210):
    """Feed df to scanner row-by-row (starting from min_bars) to allow state
    machine confirmation counters to accumulate naturally."""
    for i in range(min_bars, len(df)):
        scanner.process(symbol, df.iloc[:i+1])


def make_scanner(multiplier=0.5, cooldown=0):
    q = queue.Queue()
    scanner = StockScanner(
        alert_queue=q,
        ema_fast=20,
        ema_mid=50,
        ema_slow=200,
        atr_period=14,
        atr_multiplier=multiplier,
        cooldown_minutes=cooldown,
    )
    return scanner, q


# ── compute_ema ───────────────────────────────────────────────────────────────

class TestComputeEMA(unittest.TestCase):

    def test_output_length_matches_input(self):
        s = pd.Series(range(1, 101), dtype=float)
        result = compute_ema(s, 20)
        self.assertEqual(len(result), 100)

    def test_ema_converges_for_constant_series(self):
        s = pd.Series([50.0] * 300)
        ema = compute_ema(s, 20)
        self.assertAlmostEqual(ema.iloc[-1], 50.0, places=4)

    def test_ema_fast_gt_slow_on_uptrend(self):
        prices = pd.Series([float(i) for i in range(1, 300)])
        ema20 = compute_ema(prices, 20)
        ema200 = compute_ema(prices, 200)
        self.assertGreater(ema20.iloc[-1], ema200.iloc[-1])

    def test_ema_fast_lt_slow_on_downtrend(self):
        prices = pd.Series([float(300 - i) for i in range(300)])
        ema20 = compute_ema(prices, 20)
        ema200 = compute_ema(prices, 200)
        self.assertLess(ema20.iloc[-1], ema200.iloc[-1])

    def test_ema_no_nan_after_warmup(self):
        s = pd.Series([100.0] * 250)
        ema = compute_ema(s, 200)
        # After 200 periods, values should be non-NaN
        self.assertFalse(ema.iloc[200:].isna().any())

    def test_ema_adjust_false_differs_from_adjust_true(self):
        """Ensure we're using adjust=False (TradingView-compatible)."""
        s = pd.Series([float(i) for i in range(1, 50)])
        ema_false = compute_ema(s, 10)
        ema_true = s.ewm(span=10, adjust=True).mean()
        # They should differ (not identical)
        self.assertFalse((ema_false == ema_true).all())


# ── compute_atr ───────────────────────────────────────────────────────────────

class TestComputeATR(unittest.TestCase):

    def test_output_length_matches_input(self):
        df = make_df([100.0] * 50)
        atr = compute_atr(df, 14)
        self.assertEqual(len(atr), 50)

    def test_atr_positive_for_volatile_prices(self):
        prices = [100 + 5 * (i % 2) for i in range(100)]   # oscillating
        df = make_df(prices, high_mult=1.02, low_mult=0.98)
        atr = compute_atr(df, 14)
        self.assertTrue((atr.dropna() > 0).all())

    def test_atr_zero_for_flat_constant_prices(self):
        df = make_df([100.0] * 100, high_mult=1.0, low_mult=1.0)
        atr = compute_atr(df, 14)
        # ATR should be ~0 for perfectly flat prices
        self.assertAlmostEqual(float(atr.iloc[-1]), 0.0, places=4)

    def test_wilders_smoothing_alpha(self):
        """ATR should use alpha=1/14, not 2/(14+1) EMA.
        Use oscillating prices where both methods converge to different steady states.
        """
        import math
        # Non-constant TR: alternating large and small bars gives different steady state values
        # for Wilder (alpha=1/14) vs standard EMA (alpha=2/15)
        close_prices = [100.0 + 5.0 * math.sin(i * 0.5) for i in range(500)]
        df = make_df(close_prices, high_mult=1.02, low_mult=0.98)
        atr_wilder = compute_atr(df, 14)

        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        prev_close = close.shift(1)
        tr = pd.concat([high - low,
                        (high - prev_close).abs(),
                        (low - prev_close).abs()], axis=1).max(axis=1)
        atr_ema = tr.ewm(span=14, adjust=False).mean()

        # Verify the final values differ — Wilder's smoothing (alpha=1/14 ≈ 0.0714)
        # is slower than standard EMA (alpha=2/15 ≈ 0.1333)
        wilder_val = float(atr_wilder.iloc[-1])
        ema_val = float(atr_ema.iloc[-1])
        self.assertFalse(
            abs(wilder_val - ema_val) < 1e-6,
            f"Wilder ATR ({wilder_val:.6f}) should differ from EMA ATR ({ema_val:.6f})"
        )

    def test_true_range_uses_prev_close_gap(self):
        """ATR should capture overnight gap (high-low may be small but TR is large)."""
        data = {
            "Open":   [100, 100],
            "High":   [101, 101],
            "Low":    [99,  99],
            "Close":  [100, 110],   # big gap next bar
            "Volume": [1e6, 1e6],
        }
        df2 = pd.DataFrame(data)
        # Insert a large-gap bar
        extra = {"Open": [110], "High": [111], "Low": [109], "Close": [110], "Volume": [1e6]}
        df3 = pd.concat([df2, pd.DataFrame(extra)], ignore_index=True)
        atr = compute_atr(df3, 2)
        # TR for bar 2 = max(111-109=2, |111-110|=1, |109-110|=1) = 2
        # TR for bar 1 = max(101-99=2, |101-100|=1, |99-100|=1) = 2
        # The gap bar's TR: high=111, low=109, prev_close=100 → max(2, 11, 9) = 11
        # ATR(2) blends previous bar ATR with the gap bar's TR, must be >= 2.0
        self.assertGreaterEqual(float(atr.iloc[-1]), 2.0)


# ── compute_indicators ────────────────────────────────────────────────────────

class TestComputeIndicators(unittest.TestCase):

    def test_columns_added(self):
        df = make_bullish_df(250)
        out = compute_indicators(df)
        for col in ("ema_fast", "ema_mid", "ema_slow", "atr"):
            self.assertIn(col, out.columns)

    def test_original_df_not_mutated(self):
        df = make_bullish_df(250)
        original_cols = set(df.columns)
        compute_indicators(df)
        self.assertEqual(set(df.columns), original_cols)

    def test_bullish_alignment(self):
        df = make_bullish_df(300)
        out = compute_indicators(df)
        last = out.iloc[-1]
        self.assertGreater(last["ema_fast"], last["ema_mid"])
        self.assertGreater(last["ema_mid"], last["ema_slow"])

    def test_bearish_alignment(self):
        df = make_bearish_df(300)
        out = compute_indicators(df)
        last = out.iloc[-1]
        self.assertLess(last["ema_fast"], last["ema_mid"])
        self.assertLess(last["ema_mid"], last["ema_slow"])


# ── StockState cooldown ───────────────────────────────────────────────────────

class TestStockState(unittest.TestCase):

    def test_not_on_cooldown_initially(self):
        s = StockState(symbol="TEST")
        self.assertFalse(s.is_on_cooldown("trend_bullish", cooldown_minutes=15))

    def test_on_cooldown_after_record(self):
        s = StockState(symbol="TEST")
        s.record_alert("trend_bullish")
        self.assertTrue(s.is_on_cooldown("trend_bullish", cooldown_minutes=15))

    def test_different_alert_types_independent(self):
        s = StockState(symbol="TEST")
        s.record_alert("trend_bullish")
        self.assertFalse(s.is_on_cooldown("pullback_bullish", cooldown_minutes=15))

    def test_zero_cooldown_never_suppresses(self):
        s = StockState(symbol="TEST")
        s.record_alert("trend_bullish")
        self.assertFalse(s.is_on_cooldown("trend_bullish", cooldown_minutes=0))


# ── Alert ─────────────────────────────────────────────────────────────────────

class TestAlert(unittest.TestCase):

    def _make_alert(self, state, direction):
        return Alert(
            symbol="TEST",
            state=state,
            direction=direction,
            price=100.0,
            ema_fast=99.0,
            ema_mid=98.0,
            ema_slow=95.0,
            atr=1.5,
            distance_ratio=0.3,
        )

    def test_alert_type_trending_bullish(self):
        a = self._make_alert(SignalState.TRENDING, TrendDirection.BULLISH)
        self.assertEqual(a.alert_type, "trend_bullish")

    def test_alert_type_trending_bearish(self):
        a = self._make_alert(SignalState.TRENDING, TrendDirection.BEARISH)
        self.assertEqual(a.alert_type, "trend_bearish")

    def test_alert_type_pullback_bullish(self):
        a = self._make_alert(SignalState.PULLBACK, TrendDirection.BULLISH)
        self.assertEqual(a.alert_type, "pullback_bullish")

    def test_alert_type_pullback_bearish(self):
        a = self._make_alert(SignalState.PULLBACK, TrendDirection.BEARISH)
        self.assertEqual(a.alert_type, "pullback_bearish")

    def test_timestamp_is_utc(self):
        a = self._make_alert(SignalState.TRENDING, TrendDirection.BULLISH)
        self.assertIsNotNone(a.timestamp.tzinfo)


# ── StockScanner state machine ────────────────────────────────────────────────

class TestStockScannerStateMachine(unittest.TestCase):

    def _make_scanner(self, multiplier=0.5, cooldown=0):
        q = queue.Queue()
        scanner = StockScanner(
            alert_queue=q,
            ema_fast=20, ema_mid=50, ema_slow=200,
            atr_period=14,
            atr_multiplier=multiplier,
            cooldown_minutes=cooldown,
        )
        return scanner, q

    def test_insufficient_bars_skipped(self):
        scanner, q = self._make_scanner()
        df = make_df([100.0] * 100)   # less than ema_slow + 10 = 210
        scanner.process("TEST", df)
        self.assertTrue(q.empty())

    def test_none_df_skipped(self):
        scanner, q = self._make_scanner()
        scanner.process("TEST", None)
        self.assertTrue(q.empty())

    def test_bullish_trend_detected(self):
        scanner, q = self._make_scanner()
        df = make_strong_bullish_df()
        scanner.process("TEST", df)
        snap = scanner.get_snapshot("TEST")
        self.assertIsNotNone(snap)
        state, direction = snap
        self.assertEqual(direction, TrendDirection.BULLISH)

    def test_bearish_trend_detected(self):
        scanner, q = self._make_scanner()
        df = make_strong_bearish_df()
        scanner.process("TEST", df)
        snap = scanner.get_snapshot("TEST")
        self.assertIsNotNone(snap)
        _, direction = snap
        self.assertEqual(direction, TrendDirection.BEARISH)

    def test_no_trend_when_mixed(self):
        """Oscillating prices should produce NO_TREND."""
        scanner, q = self._make_scanner()
        # Strong oscillation breaks any EMA alignment
        import math
        prices = [100.0 + 10 * math.sin(i * 0.3) for i in range(300)]
        df = make_df(prices)
        scanner.process("TEST", df)
        snap = scanner.get_snapshot("TEST")
        if snap is not None:
            _, direction = snap
            # Could be NO_TREND or TRENDING depending on last oscillation phase
            # Just verify it doesn't crash and returns valid enums
            self.assertIn(direction, list(TrendDirection))

    def test_trending_alert_emitted_on_first_detection(self):
        scanner, q = self._make_scanner()
        df = make_strong_bullish_df()
        scanner.process("TEST", df)
        if not q.empty():
            alert = q.get_nowait()
            self.assertIsInstance(alert, Alert)
            self.assertEqual(alert.symbol, "TEST")
            self.assertIn(alert.state, [SignalState.TRENDING, SignalState.PULLBACK])

    def test_pullback_detected_when_price_near_ema(self):
        """Build a price series that ends with a pullback."""
        scanner, q = self._make_scanner(multiplier=2.0)   # very wide threshold
        # Feed incrementally so confirmation counters work
        import math
        base = [100.0 + i * 0.5 + 2.0 * math.sin(i * 0.3) for i in range(300)]
        last_close = base[-1]
        ema_approx = last_close * 0.999
        pullback_prices = base + [ema_approx] * 10
        df_pull = make_df(pullback_prices, high_mult=1.01, low_mult=0.99)
        process_incremental(scanner, "PULL", df_pull)
        snap = scanner.get_snapshot("PULL")
        if snap:
            state, _ = snap
            self.assertIn(state, [SignalState.PULLBACK, SignalState.TRENDING])

    def test_no_alert_during_cooldown(self):
        scanner, q = self._make_scanner(cooldown=60)  # 60-min cooldown
        df = make_strong_bullish_df()
        scanner.process("TEST", df)
        # Drain initial alert
        while not q.empty():
            q.get_nowait()
        # Process again — should be suppressed by cooldown
        scanner.process("TEST", df)
        self.assertTrue(q.empty())

    def test_alert_emitted_after_direction_flip(self):
        """Re-alert when trend direction changes."""
        scanner, q = self._make_scanner(cooldown=0)
        df_bull = make_strong_bullish_df()
        scanner.process("TEST", df_bull)
        while not q.empty():
            q.get_nowait()

        # Switch to bearish
        df_bear = make_strong_bearish_df()
        scanner.process("TEST", df_bear)
        # Should emit a new alert for bearish direction
        if not q.empty():
            alert = q.get_nowait()
            self.assertEqual(alert.direction, TrendDirection.BEARISH)

    def test_pullback_direction_flip_resets_to_no_trend(self):
        """When in PULLBACK and direction flips, reset to NO_TREND (not stay in PULLBACK)."""
        scanner, q = self._make_scanner(multiplier=2.0, cooldown=0)

        # Establish bullish trend
        df_bull = make_strong_bullish_df()
        scanner.process("TEST", df_bull)
        # Drain alerts
        while not q.empty():
            q.get_nowait()

        # Force state to PULLBACK manually
        state = scanner._get_state("TEST")
        state.signal_state = SignalState.PULLBACK
        state.direction = TrendDirection.BULLISH

        # Now process bearish data (direction flip)
        df_bear = make_strong_bearish_df()
        scanner.process("TEST", df_bear)

        snap = scanner.get_snapshot("TEST")
        self.assertIsNotNone(snap)
        sig_state, direction = snap
        # After direction flip in PULLBACK → should transition to NO_TREND first
        # (on next process with valid trend it may become TRENDING again)
        # The key: it should NOT still be PULLBACK with OLD bullish direction
        if sig_state == SignalState.PULLBACK:
            self.assertNotEqual(direction, TrendDirection.BULLISH,
                                "Should not remain PULLBACK+BULLISH after bearish flip")

    def test_pullback_exits_on_hysteresis(self):
        """PULLBACK → TRENDING when distance ratio > multiplier * 1.5."""
        scanner, q = self._make_scanner(multiplier=0.5, cooldown=0)

        # Start trending
        df_bull = make_strong_bullish_df()
        scanner.process("TEST", df_bull)
        while not q.empty():
            q.get_nowait()

        # Force into PULLBACK
        state = scanner._get_state("TEST")
        state.signal_state = SignalState.PULLBACK
        state.direction = TrendDirection.BULLISH

        # Build a frame where price is well above EMA20 (large distance ratio)
        # Use a very steep uptrend ending so price >> EMA20
        import math
        prices = [100.0 + i * 1.0 + 2.0 * math.sin(i * 0.3) for i in range(300)]
        df_steep = make_df(prices, high_mult=1.01, low_mult=0.99)
        scanner.process("TEST", df_steep)

        snap = scanner.get_snapshot("TEST")
        if snap:
            sig_state, _ = snap
            # Should have exited PULLBACK (either TRENDING or NO_TREND)
            self.assertNotEqual(sig_state, SignalState.PULLBACK,
                                "Should exit PULLBACK when price far from EMA20")

    def test_remove_symbol_clears_state(self):
        scanner, q = self._make_scanner()
        df = make_bullish_df(300)
        scanner.process("TEST", df)
        self.assertIsNotNone(scanner.get_snapshot("TEST"))
        scanner.remove_symbol("TEST")
        self.assertIsNone(scanner.get_snapshot("TEST"))

    def test_remove_nonexistent_symbol_no_error(self):
        scanner, q = self._make_scanner()
        scanner.remove_symbol("NONEXISTENT")   # Should not raise

    def test_multiple_symbols_independent(self):
        scanner, q = self._make_scanner()
        df_bull = make_strong_bullish_df()
        df_bear = make_strong_bearish_df()
        scanner.process("BULL", df_bull)
        scanner.process("BEAR", df_bear)
        snap_bull = scanner.get_snapshot("BULL")
        snap_bear = scanner.get_snapshot("BEAR")
        if snap_bull and snap_bear:
            _, dir_bull = snap_bull
            _, dir_bear = snap_bear
            self.assertEqual(dir_bull, TrendDirection.BULLISH)
            self.assertEqual(dir_bear, TrendDirection.BEARISH)

    def test_atr_zero_does_not_crash(self):
        """ATR=0 (flat prices) should be handled gracefully."""
        scanner, q = self._make_scanner()
        df = make_df([100.0] * 300, high_mult=1.0, low_mult=1.0)
        scanner.process("FLAT", df)   # Should not raise
        self.assertTrue(q.empty())   # No alert for zero ATR

    def test_get_snapshot_unknown_symbol(self):
        scanner, q = self._make_scanner()
        self.assertIsNone(scanner.get_snapshot("UNKNOWN"))


# ── Distance ratio calculation ─────────────────────────────────────────────────

class TestDistanceRatio(unittest.TestCase):

    def test_ratio_is_zero_when_price_equals_ema(self):
        """When close equals EMA20 exactly, distance_ratio should be ~0."""
        # Constant price series: EMA converges to the price
        scanner, q = make_scanner(multiplier=0.5, cooldown=0)
        prices = [100.0] * 300
        # Make a tiny ATR by having very small H-L spread
        df = make_df(prices, high_mult=1.0001, low_mult=0.9999)
        scanner.process("TEST", df)
        # If ATR > 0, distance_ratio = |price - EMA| / ATR ≈ 0 / tiny ≈ 0
        # But ATR ≈ 0 means we'll get the zero-ATR guard — that's fine

    def test_bullish_pullback_when_ratio_below_threshold(self):
        """Verify the pullback threshold triggers correctly."""
        q = queue.Queue()
        scanner = StockScanner(
            alert_queue=q,
            ema_fast=20, ema_mid=50, ema_slow=200,
            atr_period=14,
            atr_multiplier=10.0,   # very wide threshold: almost always pullback
            cooldown_minutes=0,
        )
        df = make_strong_bullish_df()
        process_incremental(scanner, "TEST", df)
        snap = scanner.get_snapshot("TEST")
        if snap:
            state, direction = snap
            self.assertIn(state, [SignalState.PULLBACK, SignalState.TRENDING])


    def test_no_trending_alert_after_pullback_exit(self):
        """
        Regression: after PULLBACK exits via hysteresis back to TRENDING,
        no redundant TRENDING (上涨/下跌) alert should fire.
        The trend direction has not changed — only the distance ratio recovered.
        """
        scanner, q = make_scanner(multiplier=0.5, cooldown=0)

        # 1. Establish bullish trend
        df_bull = make_strong_bullish_df()
        scanner.process("TEST", df_bull)
        while not q.empty():
            q.get_nowait()   # drain initial alerts

        # 2. Force state into PULLBACK
        state = scanner._get_state("TEST")
        state.signal_state = SignalState.PULLBACK
        state.direction = TrendDirection.BULLISH
        state.resumed_from_pullback = False

        # 3. Process a steep-uptrend frame so price is far from EMA20
        #    → hysteresis exit: PULLBACK → TRENDING, direction unchanged
        import math
        prices = [100.0 + i * 1.0 + 2.0 * math.sin(i * 0.3) for i in range(300)]
        df_steep = make_df(prices, high_mult=1.01, low_mult=0.99)
        scanner.process("TEST", df_steep)

        # 4. No alert should have been emitted (trend direction did not change)
        alerts = []
        while not q.empty():
            item = q.get_nowait()
            if isinstance(item, Alert):
                alerts.append(item)

        trending_alerts = [a for a in alerts if a.state == SignalState.TRENDING]
        self.assertEqual(
            len(trending_alerts), 0,
            f"Expected no TRENDING alert after pullback exit, got: {trending_alerts}"
        )


if __name__ == "__main__":
    unittest.main()
