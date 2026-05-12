"""
Sends proactive alert cards to a Microsoft Teams channel via an Incoming Webhook.

Set up once in Teams:
  Channel → … → Connectors → Incoming Webhook → create → copy URL → set TEAMS_WEBHOOK_URL
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)


def send_teams_alert(title: str, body: str, color: str = "FF0000") -> None:
    """POST a MessageCard to the configured Teams webhook. Silently skips if no URL is set."""
    url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not url:
        logger.debug("TEAMS_WEBHOOK_URL not set — skipping Teams notification")
        return

    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": title,
        "sections": [
            {
                "activityTitle": f"**{title}**",
                "activityText": body,
            }
        ],
    }

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
    except Exception as exc:
        logger.error("Failed to send Teams alert: %s", exc)
