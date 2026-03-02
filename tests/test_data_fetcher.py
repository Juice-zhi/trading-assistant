"""
Tests for data_fetcher.py — fetch_ohlcv
Uses unittest.mock to avoid real network calls.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_fetcher import fetch_ohlcv


def _make_ticker_df(rows=300, include_extra_col=False):
    """Build a realistic yfinance-style DataFrame."""
    idx = pd.date_range("2024-01-01", periods=rows, freq="1min")
    close = pd.Series([100.0 + i * 0.01 for i in range(rows)], index=idx)
    df = pd.DataFrame({
        "open":   close * 0.999,
        "high":   close * 1.005,
        "low":    close * 0.995,
        "close":  close,
        "volume": [1_000_000] * rows,
    }, index=idx)
    if include_extra_col:
        df["dividends"] = 0.0
    return df


class TestFetchOHLCV(unittest.TestCase):

    @patch("data_fetcher.yf.Ticker")
    def test_returns_dataframe_on_success(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_ticker_df(300)
        mock_ticker_cls.return_value = mock_ticker

        result = fetch_ohlcv("AAPL")
        self.assertIsInstance(result, pd.DataFrame)

    @patch("data_fetcher.yf.Ticker")
    def test_columns_capitalized(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_ticker_df(300)
        mock_ticker_cls.return_value = mock_ticker

        result = fetch_ohlcv("AAPL")
        self.assertIsNotNone(result)
        for col in ("Open", "High", "Low", "Close", "Volume"):
            self.assertIn(col, result.columns)

    @patch("data_fetcher.yf.Ticker")
    def test_extra_columns_removed(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_ticker_df(300, include_extra_col=True)
        mock_ticker_cls.return_value = mock_ticker

        result = fetch_ohlcv("AAPL")
        self.assertIsNotNone(result)
        self.assertNotIn("Dividends", result.columns)
        self.assertEqual(set(result.columns), {"Open", "High", "Low", "Close", "Volume"})

    @patch("data_fetcher.yf.Ticker")
    def test_returns_none_on_empty_dataframe(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_cls.return_value = mock_ticker

        result = fetch_ohlcv("INVALID")
        self.assertIsNone(result)

    @patch("data_fetcher.yf.Ticker")
    def test_returns_none_on_none_result(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = None
        mock_ticker_cls.return_value = mock_ticker

        result = fetch_ohlcv("INVALID")
        self.assertIsNone(result)

    @patch("data_fetcher.yf.Ticker")
    def test_returns_none_on_exception(self, mock_ticker_cls):
        mock_ticker_cls.side_effect = RuntimeError("Network error")

        result = fetch_ohlcv("AAPL")
        self.assertIsNone(result)

    @patch("data_fetcher.yf.Ticker")
    def test_drops_nan_close_rows(self, mock_ticker_cls):
        df = _make_ticker_df(300)
        # Inject NaN close values
        df.loc[df.index[50], "close"] = float("nan")
        df.loc[df.index[100], "close"] = float("nan")
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df
        mock_ticker_cls.return_value = mock_ticker

        result = fetch_ohlcv("AAPL")
        self.assertIsNotNone(result)
        self.assertFalse(result["Close"].isna().any())
        self.assertEqual(len(result), 298)

    @patch("data_fetcher.yf.Ticker")
    def test_correct_period_and_interval_passed(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_ticker_df(300)
        mock_ticker_cls.return_value = mock_ticker

        fetch_ohlcv("AAPL", period="5d", interval="1m")

        mock_ticker.history.assert_called_once_with(
            period="5d",
            interval="1m",
            prepost=False,
            auto_adjust=True,
        )

    @patch("data_fetcher.yf.Ticker")
    def test_custom_period_and_interval(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_ticker_df(100)
        mock_ticker_cls.return_value = mock_ticker

        fetch_ohlcv("SPY", period="1d", interval="5m")

        mock_ticker.history.assert_called_once_with(
            period="1d",
            interval="5m",
            prepost=False,
            auto_adjust=True,
        )

    @patch("data_fetcher.yf.Ticker")
    def test_ticker_called_with_symbol(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_ticker_df(300)
        mock_ticker_cls.return_value = mock_ticker

        fetch_ohlcv("TSLA")
        mock_ticker_cls.assert_called_once_with("TSLA")

    @patch("data_fetcher.yf.Ticker")
    def test_returns_none_when_missing_ohlcv_columns(self, mock_ticker_cls):
        """DataFrame missing required columns should return None."""
        df_missing = pd.DataFrame({"close": [100.0] * 10})
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df_missing
        mock_ticker_cls.return_value = mock_ticker

        result = fetch_ohlcv("BAD")
        self.assertIsNone(result)

    @patch("data_fetcher.yf.Ticker")
    def test_row_count_preserved_when_no_nans(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_ticker_df(1950)
        mock_ticker_cls.return_value = mock_ticker

        result = fetch_ohlcv("AAPL")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1950)


class TestFetchOHLCVIntegration(unittest.TestCase):
    """
    Optional integration test — skipped unless RUN_INTEGRATION=1 env var set.
    These make real network calls to yfinance.
    """

    @unittest.skipUnless(os.environ.get("RUN_INTEGRATION") == "1", "Integration tests skipped")
    def test_real_aapl_fetch(self):
        df = fetch_ohlcv("AAPL", period="5d", interval="1m")
        self.assertIsNotNone(df)
        self.assertGreater(len(df), 100)
        self.assertEqual(set(df.columns), {"Open", "High", "Low", "Close", "Volume"})
        self.assertFalse(df["Close"].isna().any())


if __name__ == "__main__":
    unittest.main()
