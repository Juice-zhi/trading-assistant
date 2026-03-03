"""
DataFetcher: yfinance wrapper that fetches 1-minute OHLCV data.
Uses period="5d" to get ~1950 bars, ensuring EMA200 is fully converged.
Includes pre/post-market data (prepost=True) so that EMA calculations
already reflect premarket price action at the regular-session open.
"""
import logging
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def fetch_ohlcv(symbol: str, period: str = "5d", interval: str = "1m") -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV data for a symbol via yfinance.

    prepost=True includes pre/post-market bars so the EMA is already
    "warmed up" with premarket price action by the time regular trading opens.

    Returns a DataFrame with columns [Open, High, Low, Close, Volume]
    indexed by UTC datetime, or None on failure.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, prepost=True, auto_adjust=True)

        if df is None or df.empty:
            logger.warning("No data returned for %s", symbol)
            return None

        # Normalize column names
        df.columns = [c.capitalize() for c in df.columns]

        # Keep only OHLCV
        needed = ["Open", "High", "Low", "Close", "Volume"]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            logger.warning("Missing columns %s for %s", missing, symbol)
            return None

        df = df[needed].copy()

        # Drop rows with NaN close prices
        df.dropna(subset=["Close"], inplace=True)

        if len(df) < 200:
            logger.warning("Insufficient bars (%d) for %s — EMA200 may not be converged", len(df), symbol)

        return df

    except Exception as exc:
        logger.error("fetch_ohlcv(%s) failed: %s", symbol, exc)
        return None
