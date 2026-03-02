"""
Tests for notifier.py — DiscordNotifier (plain-text format)
"""
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from notifier import DiscordNotifier, _build_content
from scanner import Alert, SignalState, TrendDirection


def make_alert(state=SignalState.TRENDING, direction=TrendDirection.BULLISH,
               symbol="AAPL", price=150.0):
    return Alert(
        symbol=symbol,
        state=state,
        direction=direction,
        price=price,
        ema_fast=149.0,
        ema_mid=148.0,
        ema_slow=145.0,
        atr=1.5,
        distance_ratio=0.3,
        timestamp=datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
    )


class TestBuildContent(unittest.TestCase):

    def test_bullish_trend_message(self):
        a = make_alert(state=SignalState.TRENDING, direction=TrendDirection.BULLISH, symbol="AAPL")
        self.assertEqual(_build_content(a), "AAPL 上涨")

    def test_bearish_trend_message(self):
        a = make_alert(state=SignalState.TRENDING, direction=TrendDirection.BEARISH, symbol="TSLA")
        self.assertEqual(_build_content(a), "TSLA 下跌")

    def test_pullback_bullish_message(self):
        a = make_alert(state=SignalState.PULLBACK, direction=TrendDirection.BULLISH, symbol="NVDA")
        self.assertEqual(_build_content(a), "NVDA 回撤EMA20")

    def test_pullback_bearish_message(self):
        a = make_alert(state=SignalState.PULLBACK, direction=TrendDirection.BEARISH, symbol="SPY")
        self.assertEqual(_build_content(a), "SPY 回撤EMA20")

    def test_symbol_in_message(self):
        a = make_alert(symbol="MSFT")
        msg = _build_content(a)
        self.assertIn("MSFT", msg)


class TestDiscordNotifierDisabled(unittest.TestCase):

    def test_disabled_notifier_returns_false(self):
        notifier = DiscordNotifier(enabled=False, webhook_url="https://example.com")
        self.assertFalse(notifier.send_alert(make_alert()))

    def test_empty_url_returns_false(self):
        notifier = DiscordNotifier(enabled=True, webhook_url="")
        self.assertFalse(notifier.send_alert(make_alert()))

    def test_disabled_and_empty_returns_false(self):
        notifier = DiscordNotifier(enabled=False, webhook_url="")
        self.assertFalse(notifier.send_alert(make_alert()))

    def test_test_connection_empty_url_returns_false(self):
        self.assertFalse(DiscordNotifier(enabled=True, webhook_url="").test_connection())

    def test_test_connection_with_explicit_empty_url(self):
        self.assertFalse(DiscordNotifier().test_connection(webhook_url=""))


class TestDiscordNotifierHTTP(unittest.TestCase):

    def _resp(self, code):
        r = MagicMock()
        r.status_code = code
        r.text = ""
        return r

    @patch("notifier.requests.post")
    def test_send_alert_success_200(self, mock_post):
        mock_post.return_value = self._resp(200)
        notifier = DiscordNotifier(enabled=True, webhook_url="https://discord.com/api/webhooks/test")
        self.assertTrue(notifier.send_alert(make_alert()))
        mock_post.assert_called_once()

    @patch("notifier.requests.post")
    def test_send_alert_success_204(self, mock_post):
        mock_post.return_value = self._resp(204)
        notifier = DiscordNotifier(enabled=True, webhook_url="https://discord.com/api/webhooks/test")
        self.assertTrue(notifier.send_alert(make_alert()))

    @patch("notifier.requests.post")
    def test_send_alert_failure_400(self, mock_post):
        mock_post.return_value = self._resp(400)
        notifier = DiscordNotifier(enabled=True, webhook_url="https://discord.com/api/webhooks/test")
        self.assertFalse(notifier.send_alert(make_alert()))

    @patch("notifier.requests.post")
    def test_send_alert_network_error(self, mock_post):
        import requests as req_lib
        mock_post.side_effect = req_lib.RequestException("refused")
        notifier = DiscordNotifier(enabled=True, webhook_url="https://discord.com/api/webhooks/test")
        self.assertFalse(notifier.send_alert(make_alert()))

    @patch("notifier.requests.post")
    def test_payload_uses_content_not_embeds(self, mock_post):
        """New format sends plain content, not embed objects."""
        mock_post.return_value = self._resp(204)
        notifier = DiscordNotifier(enabled=True, webhook_url="https://discord.com/api/webhooks/test")
        notifier.send_alert(make_alert())
        payload = mock_post.call_args[1]["json"]
        self.assertIn("content", payload)
        self.assertNotIn("embeds", payload)

    @patch("notifier.requests.post")
    def test_payload_content_matches_build_content(self, mock_post):
        mock_post.return_value = self._resp(204)
        alert = make_alert(state=SignalState.PULLBACK, direction=TrendDirection.BULLISH, symbol="NVDA")
        notifier = DiscordNotifier(enabled=True, webhook_url="https://discord.com/api/webhooks/test")
        notifier.send_alert(alert)
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["content"], "NVDA 回撤EMA20")

    @patch("notifier.requests.post")
    def test_test_connection_success(self, mock_post):
        mock_post.return_value = self._resp(204)
        notifier = DiscordNotifier(enabled=True, webhook_url="https://discord.com/api/webhooks/test")
        self.assertTrue(notifier.test_connection())

    @patch("notifier.requests.post")
    def test_test_connection_uses_provided_url(self, mock_post):
        mock_post.return_value = self._resp(204)
        notifier = DiscordNotifier(enabled=False, webhook_url="")
        self.assertTrue(notifier.test_connection(webhook_url="https://discord.com/api/webhooks/other"))
        self.assertIn("other", mock_post.call_args[0][0])

    @patch("notifier.requests.post")
    def test_correct_webhook_url_used(self, mock_post):
        mock_post.return_value = self._resp(204)
        url = "https://discord.com/api/webhooks/123/abc"
        DiscordNotifier(enabled=True, webhook_url=url).send_alert(make_alert())
        self.assertEqual(mock_post.call_args[0][0], url)

    @patch("notifier.requests.post")
    def test_timeout_applied(self, mock_post):
        mock_post.return_value = self._resp(204)
        DiscordNotifier(enabled=True, webhook_url="https://discord.com/api/webhooks/test").send_alert(make_alert())
        self.assertGreater(mock_post.call_args[1]["timeout"], 0)


if __name__ == "__main__":
    unittest.main()
