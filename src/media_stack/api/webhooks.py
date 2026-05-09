"""Webhook firing for controller action events."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from .state import ControllerState

logger = logging.getLogger("controller_api")


class WebhookDispatcher:
    """Fires controller action-event webhooks (best-effort, non-blocking).

    Stateless aside from the module-level ``logger``; instantiated once
    at import as ``_INSTANCE`` and exposed through the
    ``_fire_webhooks`` module-level alias so existing callers
    (``api.server`` re-export, ``cli.commands.controller_serve``,
    ``tests.unit.api.test_api_error_handling``) keep their
    function-style import surface unchanged.
    """

    def fire(
        self,
        state: ControllerState,
        event: str,
        payload: dict[str, Any],
    ) -> None:
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
            except Exception as exc:
                logger.debug("Webhook delivery failed for %s: %s", url, exc)


_INSTANCE = WebhookDispatcher()
_fire_webhooks = _INSTANCE.fire
