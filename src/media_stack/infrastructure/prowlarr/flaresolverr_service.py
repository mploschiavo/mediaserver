"""Prowlarr FlareSolverr preflight/config service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from media_stack.api.services.registry import service_internal_url

BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
EnsureProxyFn = Callable[[str, str, dict[str, Any]], "int | None"]


@dataclass
class ProwlarrFlareSolverrService:
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    ensure_proxy: EnsureProxyFn

    def ensure_from_config(
        self,
        *,
        cfg: dict[str, Any],
        prowlarr_url: str,
        prowlarr_key: str,
        wait_timeout: int,
    ) -> int | None:
        """Returns the Prowlarr indexerProxy ID for the FlareSolverr
        entry so the caller can attach it to CloudFlare-protected
        indexers. Returns ``None`` when flaresolverr is disabled or
        when Prowlarr didn't echo back an id."""
        flaresolverr_cfg = cfg.get("flaresolverr") or {}
        if not isinstance(flaresolverr_cfg, dict):
            raise RuntimeError("flaresolverr config must be an object.")
        if not self.bool_cfg(flaresolverr_cfg, "enabled", False):
            return None

        flaresolverr_url = self.normalize_url(
            str(flaresolverr_cfg.get("url") or service_internal_url("flaresolverr"))
        )
        self.wait_for_service("FlareSolverr", flaresolverr_url, "/", wait_timeout)

        payload_cfg = dict(flaresolverr_cfg)
        payload_cfg["url"] = flaresolverr_url
        return self.ensure_proxy(prowlarr_url, prowlarr_key, payload_cfg)
