"""KeyCanaryValidator — Strategy for detecting config-mount mismatches at boot.

ADR-0015 Phase 7e. Pre-Phase-7e the ``_validate_key_against_service``
helper lived as a method on ``ControllerServeCommand`` in
commands/. It walks the service registry, picks the first
arr-style service with a discovered API key, and probes it via
HTTP to confirm the controller's config mount matches the
running service container's mount.

Splitting onto its own class isolates the "validate at boot"
responsibility from the HTTP-server-glue concerns that
``ControllerServeCommand`` legitimately keeps.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Callable

from media_stack.core.logging_utils import log_swallowed
from media_stack.core.service_registry.registry import SERVICES


_CANARY_PROBE_TIMEOUT_SECONDS = 3


class KeyCanaryValidator:
    """Strategy: probe one discovered key against its running service."""

    def validate(
        self,
        discovered: dict,
        config_root: str,
        log: Callable[[str], None],
    ) -> None:
        """Quick check: does a discovered key actually work against the running service?

        If not, the controller's config mount likely points to a different
        directory than the services. This is a common compose-context mismatch.
        """
        canary, canary_key = self._pick_canary(discovered)
        if canary is None:
            return
        try:
            req = urllib.request.Request(
                f"http://{canary.host}:{canary.port}{canary.auth_path}",
                headers={canary.auth_mode: canary_key},
            )
            with urllib.request.urlopen(req, timeout=_CANARY_PROBE_TIMEOUT_SECONDS) as resp:
                if resp.status == 200:
                    return  # Key works — mounts are consistent.
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                log(
                    f"[WARN] Config mount mismatch detected: API key from "
                    f"{config_root}/{canary.api_key_config} does not match the running "
                    f"{canary.name} container. This usually means the controller and "
                    "services are using different config directories. "
                    "Re-run 'docker compose down && docker compose up -d' from "
                    "the same directory to fix."
                )
                return
        except (urllib.error.URLError, OSError) as exc:
            # Service not ready yet — skip validation.
            log_swallowed(exc)

    def _pick_canary(self, discovered: dict) -> tuple[object | None, str]:
        for svc in SERVICES:
            if svc.api_key_env and svc.auth_path and svc.api_key_format == "xml":
                key = discovered.get(svc.api_key_env, "")
                if key:
                    return svc, key
        return None, ""


__all__ = ["KeyCanaryValidator"]
