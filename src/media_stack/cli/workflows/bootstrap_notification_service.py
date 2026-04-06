"""Webhook notification helper for bootstrap orchestration."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class BootstrapNotificationConfig:
    alert_webhook_url: str


@dataclass
class BootstrapNotificationService:
    cfg: BootstrapNotificationConfig

    def notify(self, status: str, message: str) -> None:
        if not self.cfg.alert_webhook_url:
            return
        payload = json.dumps({"status": status, "message": message}).encode("utf-8")
        request = urllib.request.Request(
            self.cfg.alert_webhook_url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8):
                return
        except urllib.error.URLError:
            return
