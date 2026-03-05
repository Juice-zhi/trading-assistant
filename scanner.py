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
from datetime import datetime, time as dt_time, timedelta, timezone
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
    # Confirmation counters to avoid single-bar flips
    trend_confirm_count: int = 0   # bars of consecutive EMA alignment seen
    no_trend_count: int = 0        # bars of consecutive NO_TREND seen (for exit)

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
        # Trend confirmation: require N consecutive bars of EMA alignment before
        # entering TRENDING, and M consecutive bars of misalignment before exiting.
        trend_confirm_bars: int = 3,
        trend_exit_bars: int = 2,
        # Consolidation filter
        consolidation_min_votes: int = 2,
        consolidation_bb_period: int = 20,
        consolidation_bb_threshold: float = 0.006,
        consolidation_ema_gap_atr: float = 0.5,
        consolidation_range_bars: int = 10,
        consolidation_range_threshold: float = 0.004,
    ):
        self._queue = alert_queue
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.cooldown_minutes = cooldown_minutes
        self.trend_confirm_bars = trend_confirm_bars
        self.trend_exit_bars = trend_exit_bars
        self.consolidation_min_votes = consolidation_min_votes
        self.consolidation_bb_period = consolidation_bb_period
        self.consolidation_bb_threshold = consolidation_bb_threshold
        self.consolidation_ema_gap_atr = consolidation_ema_gap_atr
        self.consolidation_range_bars = consolidation_range_bars
        self.consolidation_range_threshold = consolidation_range_threshold
        self._states: Dict[str, StockState] = {}

    def _get_state(self, symbol: str) -> StockState:
        if symbol not in self._states:
            self._states[symbol] = StockState(symbol=symbol)
        return self._states[symbol]

    def _detect_trend(
        self, price: float, ema_f: float, ema_m: float, ema_s: float
    ) -> TrendDirection:
        # Primary condition: EMA20/50 alignment + price on the correct side.
        # EMA200 is not required to have fully flipped on 1-min bars — it lags
        # too much to be useful for detecting the start of a new intraday move.
        if ema_f > ema_m and price > ema_f:
            return TrendDirection.BULLISH
        if ema_f < ema_m and price < ema_f:
            return TrendDirection.BEARISH
        return TrendDirection.NONE

    def _is_consolidating(
        self,
        df: pd.DataFrame,
        ema_f: float,
        ema_m: float,
        atr: float,
    ) -> bool:
        """
        Three-indicator voting filter to detect price consolidation/ranging.

        A symbol is considered consolidating when at least 2 of 3 conditions hold:

        1. BB width  — Bollinger Band width (Upper-Lower)/SMA is narrow,
                       indicating low volatility / tight range.

        2. EMA gap   — |EMA20 - EMA50| / ATR is small, meaning the two fast
                       EMAs are converging (trend losing momentum).

        3. Price range — (highest High - lowest Low) over recent N bars,
                         normalised by EMA50, is small.

        Indicators 1 and 3 use only regular-session bars (09:30–16:00 ET) so that
        the naturally low volatility of pre/post-market bars does not cause a false
        consolidation reading at the regular-session open.

        All thresholds are configurable; set consolidation_min_votes=0 to disable.
        """
        if self.consolidation_min_votes <= 0:
            return False

        votes = 0

        # Filter to regular-session bars only for range-based indicators so that
        # pre/post-market low-volatility bars don't inflate the consolidation signal.
        _SESSION_OPEN  = dt_time(9, 30)
        _SESSION_CLOSE = dt_time(16, 0)
        try:
            idx_time = df.index.time
            session_mask = (idx_time >= _SESSION_OPEN) & (idx_time <= _SESSION_CLOSE)
            df_session = df[session_mask]
        except Exception:
            df_session = df  # fallback: use full df if index has no .time

        # ── 1. Bollinger Band width ───────────────────────────────────────────
        bb_period = self.consolidation_bb_period
        if len(df_session) >= bb_period:
            close = df_session["Close"].iloc[-bb_period:]
            sma = float(close.mean())
            if sma > 0:
                std = float(close.std(ddof=1))
                bb_width = (2 * 2 * std) / sma   # (upper - lower) / sma, 2σ bands
                if bb_width < self.consolidation_bb_threshold:
                    votes += 1

        # ── 2. EMA convergence ────────────────────────────────────────────────
        if atr > 0:
            ema_gap_ratio = abs(ema_f - ema_m) / atr
            if ema_gap_ratio < self.consolidation_ema_gap_atr:
                votes += 1

        # ── 3. Recent price range ─────────────────────────────────────────────
        n = self.consolidation_range_bars
        if len(df_session) >= n and ema_m > 0:
            recent_high = float(df_session["High"].iloc[-n:].max())
            recent_low  = float(df_session["Low"].iloc[-n:].min())
            range_ratio = (recent_high - recent_low) / ema_m
            if range_ratio < self.consolidation_range_threshold:
                votes += 1

        consolidating = votes >= self.consolidation_min_votes
        if consolidating:
            logger.debug(
                "%s consolidation votes=%d (bb/gap/range need %d)",
                "symbol", votes, self.consolidation_min_votes,
            )
        return consolidating

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

        consolidating = self._is_consolidating(df, ema_f, ema_m, atr)
        if consolidating:
            current_trend = TrendDirection.NONE
            logger.debug("%s: consolidation detected, suppressing trend signal", symbol)

        state = self._get_state(symbol)
        alert = self._step(symbol, state, df, indicators, current_trend,
                           price, ema_f, ema_m, ema_s, atr, distance_ratio,
                           emit_alert=True)
        if alert is not None:
            self._queue.put(alert)
            logger.info("Alert queued: %s %s %s", symbol, alert.state.name, alert.direction.value)

    def warm_up(self, symbol: str, df: pd.DataFrame) -> None:
        """
        Replay the last N regular-session bars to bring the state machine up to
        the correct current state without emitting any alerts.

        Called once per symbol at startup so that a symbol already in a trend is
        correctly recognised rather than starting from NO_TREND.
        """
        if df is None or len(df) < self.ema_slow + 10:
            return

        indicators = compute_indicators(
            df,
            ema_fast=self.ema_fast,
            ema_mid=self.ema_mid,
            ema_slow=self.ema_slow,
            atr_period=self.atr_period,
        )

        # Determine replay window: enough bars to allow the state machine to
        # converge — trend_confirm_bars + trend_exit_bars + a small buffer.
        replay_bars = max(self.trend_confirm_bars + self.trend_exit_bars + 5, 20)

        # Restrict replay to regular-session bars so premarket low-volatility
        # rows don't create a spurious NO_TREND on startup.
        _SESSION_OPEN  = dt_time(9, 30)
        _SESSION_CLOSE = dt_time(16, 0)
        try:
            idx_time = df.index.time
            session_mask = (idx_time >= _SESSION_OPEN) & (idx_time <= _SESSION_CLOSE)
            session_idx = df.index[session_mask]
            # Take the last replay_bars session bars; use the full df slice for
            # indicator values so EMA/ATR are already fully warmed up.
            if len(session_idx) == 0:
                replay_indices = df.index[-replay_bars:]
            else:
                replay_indices = session_idx[-replay_bars:]
        except Exception:
            replay_indices = df.index[-replay_bars:]

        state = self._get_state(symbol)

        for idx in replay_indices:
            row = indicators.loc[idx]
            price = float(row["Close"])
            ema_f = float(row["ema_fast"])
            ema_m = float(row["ema_mid"])
            ema_s = float(row["ema_slow"])
            atr   = float(row["atr"])
            if atr <= 0:
                continue

            distance_ratio = abs(price - ema_f) / atr
            current_trend = self._detect_trend(price, ema_f, ema_m, ema_s)

            # Use the df slice up to and including this bar for consolidation check
            df_slice = df.loc[:idx]
            consolidating = self._is_consolidating(df_slice, ema_f, ema_m, atr)
            if consolidating:
                current_trend = TrendDirection.NONE

            self._step(symbol, state, df_slice, indicators.loc[:idx],
                       current_trend, price, ema_f, ema_m, ema_s, atr,
                       distance_ratio, emit_alert=False)

        logger.info("Warm-up complete for %s: state=%s direction=%s",
                    symbol, state.signal_state.name, state.direction.value)

    def _step(
        self,
        symbol: str,
        state: "StockState",
        df: pd.DataFrame,
        indicators: pd.DataFrame,
        current_trend: TrendDirection,
        price: float,
        ema_f: float,
        ema_m: float,
        ema_s: float,
        atr: float,
        distance_ratio: float,
        emit_alert: bool,
    ) -> Optional["Alert"]:
        """Run one state-machine tick. Returns an Alert if one should be emitted."""
        prev_state = state.signal_state
        prev_direction = state.direction

        # ── State machine transitions ─────────────────────────────────────────
        if current_trend == TrendDirection.NONE:
            state.trend_confirm_count = 0
            if state.signal_state in (SignalState.TRENDING, SignalState.PULLBACK):
                state.no_trend_count += 1
                if state.no_trend_count >= self.trend_exit_bars:
                    state.signal_state = SignalState.NO_TREND
                    state.direction = TrendDirection.NONE
                    state.no_trend_count = 0
        else:
            state.no_trend_count = 0

            if state.signal_state == SignalState.NO_TREND:
                if current_trend == TrendDirection(state.direction.value) if state.direction != TrendDirection.NONE else False:
                    state.trend_confirm_count += 1
                else:
                    state.trend_confirm_count = 1
                    state.direction = current_trend

                if state.trend_confirm_count >= self.trend_confirm_bars:
                    state.signal_state = SignalState.TRENDING
                    state.trend_confirm_count = 0

            elif state.signal_state == SignalState.TRENDING:
                if current_trend != state.direction:
                    state.signal_state = SignalState.NO_TREND
                    state.direction = current_trend
                    state.trend_confirm_count = 1
                elif distance_ratio < self.atr_multiplier:
                    state.signal_state = SignalState.PULLBACK

            elif state.signal_state == SignalState.PULLBACK:
                if current_trend != state.direction:
                    state.signal_state = SignalState.NO_TREND
                    state.direction = TrendDirection.NONE
                    state.trend_confirm_count = 0
                elif distance_ratio > self.atr_multiplier * 1.5:
                    state.signal_state = SignalState.TRENDING
                    state.direction = current_trend
                    state.resumed_from_pullback = True

        if not emit_alert:
            return None

        # ── Emit alerts ───────────────────────────────────────────────────────
        alert: Optional[Alert] = None

        if state.signal_state == SignalState.TRENDING:
            resumed = state.resumed_from_pullback
            if resumed:
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

        return alert

    def get_snapshot(self, symbol: str) -> Optional[Tuple[SignalState, TrendDirection]]:
        """Return current (state, direction) for a symbol, or None if unknown."""
        s = self._states.get(symbol)
        if s is None:
            return None
        return (s.signal_state, s.direction)

    def remove_symbol(self, symbol: str) -> None:
        self._states.pop(symbol, None)
