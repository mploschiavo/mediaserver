"""Health and command-trigger bootstrap service logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

HttpRequestFn = Callable[..., tuple[int, Any, str]]
LogFn = Callable[[str], None]


@dataclass
class HealthService:
    http_request: HttpRequestFn
    log: LogFn

    def trigger_health_check(
        self, app_name: str, app_url: str, api_base: str, api_key: str
    ) -> None:
        status, _, body = self.http_request(
            app_url,
            f"{api_base}/command",
            api_key=api_key,
            method="POST",
            payload={"name": "CheckHealth"},
        )
        if status in (200, 201, 202):
            self.log(f"[OK] {app_name}: triggered CheckHealth")
            return
        self.log(f"[WARN] {app_name}: failed to trigger CheckHealth (HTTP {status}): {body}")

    def trigger_arr_command(
        self,
        app_name: str,
        app_url: str,
        api_base: str,
        api_key: str,
        command_name: str,
        *,
        required: bool = False,
    ) -> bool:
        # Avoid queueing duplicate long-running commands on repeated bootstrap runs.
        status, commands, body = self.http_request(
            app_url,
            f"{api_base}/command",
            api_key=api_key,
        )
        if status == 200 and isinstance(commands, list):
            for item in commands:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip().lower()
                state = str(item.get("status") or "").strip().lower()
                if name == command_name.strip().lower() and state in ("queued", "started"):
                    self.log(
                        f"[OK] {app_name}: command {command_name} already {state}; "
                        "skipping duplicate trigger"
                    )
                    return True

        status, _, body = self.http_request(
            app_url,
            f"{api_base}/command",
            api_key=api_key,
            method="POST",
            payload={"name": command_name},
        )
        if status in (200, 201, 202):
            self.log(f"[OK] {app_name}: triggered {command_name}")
            return True

        message = f"{app_name}: failed to trigger {command_name} (HTTP {status}): {body}"
        if required:
            raise RuntimeError(message)
        self.log(f"[WARN] {message}")
        return False
