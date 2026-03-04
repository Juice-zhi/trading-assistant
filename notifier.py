"""
DiscordNotifier: sends plain-text alerts via Discord webhook.
Format:
  Trend:   "AAPL 上涨"  /  "AAPL 下跌"
  Pullback:"AAPL 回撤EMA20"
"""
import logging
from typing import Optional

import requests

from scanner import Alert, SignalState, TrendDirection

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


def _build_content(alert: Alert) -> str:
    """Return a single plain-text line for the Discord message."""
    if alert.state == SignalState.PULLBACK:
        return f"{alert.symbol} 回撤EMA20"
    if alert.direction == TrendDirection.BULLISH:
        return f"{alert.symbol} 上涨"
    return f"{alert.symbol} 下跌"


class DiscordNotifier:
    """Sends plain-text alert messages to a Discord channel via webhook URL."""

    def __init__(self, webhook_url: str = "", enabled: bool = False):
        self.webhook_url = webhook_url
        self.enabled = enabled

    def send_alert(self, alert: Alert) -> bool:
        """
        Send a plain-text alert. Returns True on success, False on failure.
        Silently no-ops if disabled or webhook_url is empty.
        """
        if not self.enabled:
            logger.debug("Discord notifier disabled, skipping alert for %s", alert.symbol)
            return False
        if not self.webhook_url:
            logger.warning("Discord notifier enabled but webhook_url is empty, skipping alert for %s", alert.symbol)
            return False

        payload = {"content": _build_content(alert)}

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=TIMEOUT_SECONDS,
            )
            if response.status_code in (200, 204):
                logger.info("Discord alert sent for %s (%s)", alert.symbol, alert.alert_type)
                return True
            else:
                logger.warning(
                    "Discord webhook returned %d for %s: %s",
                    response.status_code,
                    alert.symbol,
                    response.text[:200],
                )
                return False
        except requests.RequestException as exc:
            logger.error("Discord webhook request failed for %s: %s", alert.symbol, exc)
            return False

    def test_connection(self, webhook_url: Optional[str] = None) -> bool:
        """Send a test message to verify the webhook URL."""
        url = webhook_url or self.webhook_url
        if not url:
            return False
        try:
            response = requests.post(
                url,
                json={"content": "Trading Assistant 连接测试 ✓"},
                timeout=TIMEOUT_SECONDS,
            )
            return response.status_code in (200, 204)
        except requests.RequestException:
            return False
