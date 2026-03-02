"""
Scanner: EMA/ATR indicator calculations + state machine per symbol.

State machine per symbol:
  NO_TREND → TRENDING (when EMA alignment + price condition met)
  TRENDING → PULLBACK (when |price - EMA20| / ATR < X)
  PULLBACK → TRENDING (when ratio > X * 1.5, hysteresis)
  TRENDING → NO_TREND (when EMA alignment breaks)
  PULLBACK → NO_TREND (when EMA alignment breaks)
"""
import logging
import queue
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Dict, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────

class TrendDirection(Enum):
    NONE = "none"
    BULLISH = "bullish"
    BEARISH = "bearish"


class SignalState(Enum):
    NO_TREND = auto()
    TRENDING = auto()
    PULLBACK = auto()


# ── Indicator calculations ────────────────────────────────────────────────────

def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """EMA using adjust=False to match TradingView."""
    return series.ewm(span=period, adjust=False).mean()


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR using Wilder's smoothing (alpha=1/period)."""
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder's smoothing: ewm with alpha=1/period
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def compute_indicators(
    df: pd.DataFrame,
    ema_fast: int = 20,
    ema_mid: int = 50,
    ema_slow: int = 200,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Add EMA and ATR columns to a copy of df."""
    out = df.copy()
    out["ema_fast"] = compute_ema(out["Close"], ema_fast)
    out["ema_mid"] = compute_ema(out["Close"], ema_mid)
    out["ema_slow"] = compute_ema(out["Close"], ema_slow)
    out["atr"] = compute_atr(out, atr_period)
    return out


# ── Alert data ────────────────────────────────────────────────────────────────

@dataclass
class Alert:
    symbol: str
    state: SignalState
    direction: TrendDirection
    price: float
    ema_fast: float
    ema_mid: float
    ema_slow: float
    atr: float
    distance_ratio: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def alert_type(self) -> str:
        if self.state == SignalState.PULLBACK:
            return f"pullback_{self.direction.value}"
        return f"trend_{self.direction.value}"


# ── Per-symbol state ──────────────────────────────────────────────────────────

@dataclass
class StockState:
    symbol: str
    signal_state: SignalState = SignalState.NO_TREND
    direction: TrendDirection = TrendDirection.NONE
    resumed_from_pullback: bool = False
    last_alerts: Dict[str, datetime] = field(default_factory=dict)

    def is_on_cooldown(self, alert_type: str, cooldown_minutes: int) -> bool:
        last = self.last_alerts.get(alert_type)
        if last is None:
            return False
        return datetime.now(timezone.utc) - last < timedelta(minutes=cooldown_minutes)

    def record_alert(self, alert_type: str) -> None:
        self.last_alerts[alert_type] = datetime.now(timezone.utc)


# ── Scanner ───────────────────────────────────────────────────────────────────

class StockScanner:
    """Manages per-symbol state machines and emits alerts to a queue."""

    def __init__(
        self,
        alert_queue: queue.Queue,
        ema_fast: int = 20,
        ema_mid: int = 50,
        ema_slow: int = 200,
        atr_period: int = 14,
        atr_multiplier: float = 0.5,
        cooldown_minutes: int = 15,
        consolidation_slope_threshold: float = 0.001,
        consolidation_lookback: int = 10,
    ):
        self._queue = alert_queue
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.cooldown_minutes = cooldown_minutes
        self.consolidation_slope_threshold = consolidation_slope_threshold
        self.consolidation_lookback = consolidation_lookback
        self._states: Dict[str, StockState] = {}

    def _get_state(self, symbol: str) -> StockState:
        if symbol not in self._states:
            self._states[symbol] = StockState(symbol=symbol)
        return self._states[symbol]

    def _detect_trend(
        self, price: float, ema_f: float, ema_m: float, ema_s: float
    ) -> TrendDirection:
        if ema_f > ema_m > ema_s and price > ema_f:
            return TrendDirection.BULLISH
        if ema_f < ema_m < ema_s and price < ema_f:
            return TrendDirection.BEARISH
        return TrendDirection.NONE

    def _is_consolidating(self, ema_mid_series: pd.Series) -> bool:
        """
        Return True when EMA50 is essentially flat (consolidation zone).

        Slope = (ema_mid[-1] - ema_mid[-lookback]) / ema_mid[-lookback]
        If |slope| < threshold the market is considered to be consolidating
        and no new trend signal should fire.
        A threshold of 0 disables the filter entirely.
        """
        if self.consolidation_slope_threshold <= 0:
            return False
        n = self.consolidation_lookback
        if len(ema_mid_series) < n + 1:
            return False
        base = float(ema_mid_series.iloc[-(n + 1)])
        if base == 0:
            return False
        slope = (float(ema_mid_series.iloc[-1]) - base) / base
        return abs(slope) < self.consolidation_slope_threshold

    def process(self, symbol: str, df: pd.DataFrame) -> None:
        """
        Compute indicators on df, run state machine, and push alerts to queue.
        df must have columns: Open, High, Low, Close, Volume.
        """
        if df is None or len(df) < self.ema_slow + 10:
            logger.debug("Skipping %s — insufficient bars (%s)", symbol, len(df) if df is not None else 0)
            return

        indicators = compute_indicators(
            df,
            ema_fast=self.ema_fast,
            ema_mid=self.ema_mid,
            ema_slow=self.ema_slow,
            atr_period=self.atr_period,
        )

        last = indicators.iloc[-1]
        price = float(last["Close"])
        ema_f = float(last["ema_fast"])
        ema_m = float(last["ema_mid"])
        ema_s = float(last["ema_slow"])
        atr = float(last["atr"])

        if atr <= 0:
            logger.debug("ATR is zero for %s, skipping", symbol)
            return

        distance_ratio = abs(price - ema_f) / atr
        current_trend = self._detect_trend(price, ema_f, ema_m, ema_s)

        # ── Consolidation filter ──────────────────────────────────────────────
        # If EMA50 slope is flat, treat as no-trend regardless of EMA alignment.
        # This prevents signalling a trend that is actually just chop/range.
        consolidating = self._is_consolidating(indicators["ema_mid"])
        if consolidating:
            current_trend = TrendDirection.NONE
            logger.debug("%s: consolidation detected, suppressing trend signal", symbol)

        state = self._get_state(symbol)

        prev_state = state.signal_state
        prev_direction = state.direction

        # ── State machine transitions ─────────────────────────────────────────
        if current_trend == TrendDirection.NONE:
            # Trend broken — reset
            state.signal_state = SignalState.NO_TREND
            state.direction = TrendDirection.NONE

        elif state.signal_state == SignalState.NO_TREND:
            # Fresh trend detection
            state.signal_state = SignalState.TRENDING
            state.direction = current_trend

        elif state.signal_state == SignalState.TRENDING:
            if current_trend != state.direction:
                # Direction flipped
                state.direction = current_trend

            if distance_ratio < self.atr_multiplier:
                state.signal_state = SignalState.PULLBACK

        elif state.signal_state == SignalState.PULLBACK:
            if current_trend == TrendDirection.NONE:
                state.signal_state = SignalState.NO_TREND
                state.direction = TrendDirection.NONE
            elif current_trend != state.direction:
                # Trend direction flipped while in pullback — treat as new trend
                state.signal_state = SignalState.NO_TREND
                state.direction = TrendDirection.NONE
            elif distance_ratio > self.atr_multiplier * 1.5:
                # Hysteresis: price moved away from EMA20, trend continues.
                # Mark as resuming so alert logic knows not to re-notify.
                state.signal_state = SignalState.TRENDING
                state.direction = current_trend
                state.resumed_from_pullback = True
        # ── Emit alerts ───────────────────────────────────────────────────────
        alert: Optional[Alert] = None

        if state.signal_state == SignalState.TRENDING:
            resumed = state.resumed_from_pullback
            if resumed:
                # Clear the flag but don't fire an alert — trend was already known
                state.resumed_from_pullback = False
            elif prev_state != SignalState.TRENDING or prev_direction != state.direction:
                alert = Alert(
                    symbol=symbol,
                    state=SignalState.TRENDING,
                    direction=state.direction,
                    price=price,
                    ema_fast=ema_f,
                    ema_mid=ema_m,
                    ema_slow=ema_s,
                    atr=atr,
                    distance_ratio=distance_ratio,
                )

        elif state.signal_state == SignalState.PULLBACK and prev_state != SignalState.PULLBACK:
            alert = Alert(
                symbol=symbol,
                state=SignalState.PULLBACK,
                direction=state.direction,
                price=price,
                ema_fast=ema_f,
                ema_mid=ema_m,
                ema_slow=ema_s,
                atr=atr,
                distance_ratio=distance_ratio,
            )

        if alert is not None:
            if not state.is_on_cooldown(alert.alert_type, self.cooldown_minutes):
                state.record_alert(alert.alert_type)
                self._queue.put(alert)
                logger.info("Alert queued: %s %s %s", symbol, alert.state.name, alert.direction.value)
            else:
                logger.debug("Alert suppressed (cooldown): %s %s", symbol, alert.alert_type)

    def get_snapshot(self, symbol: str) -> Optional[Tuple[SignalState, TrendDirection]]:
        """Return current (state, direction) for a symbol, or None if unknown."""
        s = self._states.get(symbol)
        if s is None:
            return None
        return (s.signal_state, s.direction)

    def remove_symbol(self, symbol: str) -> None:
        self._states.pop(symbol, None)
