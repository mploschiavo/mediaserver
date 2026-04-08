"""Webhook firing for controller action events."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from .state import ControllerState


def _fire_webhooks(state: ControllerState, event: str, payload: dict[str, Any]) -> None:
    """Fire webhooks for action events (best-effort, non-blocking)."""
    urls = list(state.webhook_urls)
    if not urls:
        return
    data = json.dumps({"event": event, **payload}).encode("utf-8")
    for url in urls:
        try:
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
