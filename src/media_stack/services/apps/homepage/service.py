"""Homepage services.yaml writer service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
                pass
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
