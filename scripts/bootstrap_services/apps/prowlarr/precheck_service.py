"""Prowlarr readiness + auth precheck service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
WaitForServiceFn = Callable[[str, str, str, int], None]
DetectArrApiBaseFn = Callable[[str, str, str], str]
EnsureAppAuthFn = Callable[..., None]


@dataclass
class ProwlarrPrecheckService:
    log: LogFn
    bool_cfg: BoolCfgFn
    wait_for_service: WaitForServiceFn
    detect_arr_api_base: DetectArrApiBaseFn
    ensure_app_auth_settings: EnsureAppAuthFn

    def ensure_ready(
        self,
        *,
        prowlarr_url: str,
        prowlarr_key: str,
        app_auth_cfg: dict[str, Any],
        wait_timeout: int,
    ) -> str:
        self.wait_for_service("Prowlarr", prowlarr_url, "/ping", wait_timeout)
        prowlarr_api_base = self.detect_arr_api_base(
            "Prowlarr",
            prowlarr_url,
            prowlarr_key,
        )
        try:
            self.ensure_app_auth_settings(
                "Prowlarr",
                "Prowlarr",
                prowlarr_url,
                prowlarr_api_base,
                prowlarr_key,
                app_auth_cfg,
            )
        except Exception as exc:
            if self.bool_cfg(app_auth_cfg, "fail_on_error", False):
                raise
            self.log(f"[WARN] Prowlarr: auth bootstrap skipped ({exc})")
        return prowlarr_api_base
