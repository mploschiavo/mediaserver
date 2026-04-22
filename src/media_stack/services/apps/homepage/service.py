"""Homepage services.yaml writer service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import logging

BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ResolvePathFn = Callable[[str | Path, str], Path]
LogFn = Callable[[str], None]
RenderHomepageServicesYamlFn = Callable[..., str]


@dataclass
class HomepageService:
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    resolve_path: ResolvePathFn
    log: LogFn
    default_hosts: list[str]
    render_services_yaml: RenderHomepageServicesYamlFn

    def ensure_services_config(self, cfg: dict[str, Any], config_root: str) -> bool:
        homepage_cfg = cfg.get("homepage") or {}
        hosts = [
            str(host).strip().lower()
            for host in self.coerce_list(homepage_cfg.get("hosts"))
            if str(host).strip()
        ]
        enabled = self.bool_cfg(homepage_cfg, "enabled", True) or bool(hosts)
        if not enabled:
            return False

        scheme = str(homepage_cfg.get("scheme", "")).strip().lower()
        services_rel_path = str(
            homepage_cfg.get("services_relative_path") or "homepage/services.yaml"
        ).strip()
        services_path = self.resolve_path(config_root, services_rel_path)
        services_path.parent.mkdir(parents=True, exist_ok=True)

        if not hosts:
            hosts = list(self.default_hosts)

        # Filter to services that BOTH (a) the registry knows about
        # and (b) are enabled in the active deploy. Two failure
        # modes this catches:
        #   1. Profile-gated services (Authelia, Authentik, Plex)
        #      whose compose profile isn't active.
        #   2. Stub/placeholder hosts that exist in DEFAULT_HOSTS
        #      but have no contract entry (e.g. recyclarr ships as
        #      a `hashicorp/http-echo` stub in dist/docker-compose.yml
        #      saying "Enable and configure a real Recyclarr runtime
        #      when ready" — definitely shouldn't get a homepage
        #      tile).
        # Hostnames like ``authelia.local`` map to service id
        # ``authelia``; we look up by that first label. A separate
        # ratchet (``test_homepage_default_hosts_in_registry``) keeps
        # DEFAULT_HOSTS aligned with the registry so an addition
        # there doesn't silently disappear from the tile list.
        try:
            from media_stack.api.services.registry import (
                SERVICE_MAP, is_service_enabled,
            )
            filtered: list[str] = []
            for h in hosts:
                svc_id = h.split(".", 1)[0]
                svc = SERVICE_MAP.get(svc_id)
                if svc is not None and is_service_enabled(svc):
                    filtered.append(h)
            hosts = filtered
        except Exception as exc:
            # Registry import shouldn't fail in production; if it
            # does (broken contracts dir, etc.) fall back to the
            # unfiltered list rather than break homepage rendering.
            self.log(f"[WARN] Homepage host filter skipped: {exc}")

        onboarding_cfg = homepage_cfg.get("device_onboarding")
        if not isinstance(onboarding_cfg, dict):
            onboarding_cfg = {}

        # Build gateway base URL from routing config so tiles link to
        # real browser-reachable URLs (e.g. http://apps.media-stack.local/app/sonarr).
        # Use ProfileConfig as the source of truth.
        routing_cfg = cfg.get("routing") or {}
        if not routing_cfg.get("gateway_host"):
            try:
                from media_stack.services.profile_config import get_profile_config
                profile = get_profile_config()
                routing_cfg = {
                    "gateway_host": profile.routing.gateway_host,
                    "gateway_port": profile.routing.gateway_port,
                    "app_path_prefix": profile.routing.app_path_prefix,
                    "scheme": profile.routing.resolved_scheme,
                }
            except Exception:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
        gateway_host = str(routing_cfg.get("gateway_host", "")).strip()
        gateway_port = str(routing_cfg.get("gateway_port", "")).strip()
        app_path_prefix = str(routing_cfg.get("app_path_prefix", "/app")).strip()
        # Resolve scheme: homepage config > routing config > default http
        if not scheme:
            scheme = str(routing_cfg.get("scheme", "")).strip().lower()
        if not scheme:
            scheme = "https" if str(gateway_port) == "443" else "http"
        gateway_base_url = ""
        if gateway_host:
            port_suffix = ""
            if gateway_port and gateway_port not in ("80", "443"):
                port_suffix = f":{gateway_port}"
            gateway_base_url = f"{scheme}://{gateway_host}{port_suffix}"

        rendered = self.render_services_yaml(
            hosts,
            scheme=scheme,
            onboarding=onboarding_cfg,
            gateway_base_url=gateway_base_url,
            app_path_prefix=app_path_prefix,
        )
        current = (
            services_path.read_text(encoding="utf-8", errors="replace")
            if services_path.exists()
            else ""
        )
        if current == rendered:
            self.log(f"[OK] Homepage: services config already up-to-date at {services_path}")
            return False

        services_path.write_text(rendered, encoding="utf-8")
        self.log(f"[OK] Homepage: wrote services config {services_path} (hosts={len(hosts)})")
        self.log("[INFO] Homepage: restart recommended to pick up updated services config.")
        return True
