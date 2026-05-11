"""DeployProfileDefaultsExtractor — Strategy for ControllerProfileConfig→dict.

The deploy CLI accepts profile-driven defaults: the operator
points at a profile YAML, the parser reads the typed
:class:`ControllerProfileConfig` from disk, and the deploy
configuration falls back to those values when the CLI/env doesn't
specify them.

Translation of the typed profile dataclass to the env-var-shape
string dict the deploy CLI's _pick chain consumes used to live as
:meth:`_resolve_profile_defaults` on :class:`DeployCliConfigService`
— 30 lines of "for each profile field, format as the deploy CLI
expects." Phase 3c lifts it onto its own Strategy class so the
Facade gets thinner and the mapping is testable in isolation.

Strategy pattern: this class is the strategy for "given a typed
profile, what defaults dict should the deploy CLI see?". The
default implementation handles the canonical ControllerProfileConfig
shape; subclasses (or test doubles) can override for synthetic
profiles.

The output dict is string-typed throughout because the deploy
CLI's downstream chain (``_pick``, argparse defaults) consumes
strings — bool conversion happens at the final cfg-build step,
not here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from media_stack.core.controller_profile import ControllerProfileConfig


class DeployProfileDefaultsExtractor:
    """Strategy: ControllerProfileConfig → deploy-CLI defaults dict."""

    def extract(
        self,
        profile: "ControllerProfileConfig | None",
    ) -> dict[str, str]:
        """Return the env-var-shape defaults dict.

        Empty dict when ``profile`` is ``None`` (operator didn't
        point at a profile YAML, so the deploy falls back to
        argparse defaults / env vars / hardcoded fallbacks).
        """
        if profile is None:
            return {}
        ingress_domain = profile.exposure.ingress_domain
        return {
            "platform_target": profile.deployment_target,
            "namespace": profile.stack_name,
            "compose_project_name": profile.stack_name,
            "run_bootstrap": "1" if profile.preconfigure_apps else "0",
            "preconfigure_api_keys": "1" if profile.preconfigure_api_keys else "0",
            "apply_initial_preferences": "1" if profile.apply_initial_preferences else "0",
            "auto_download_content": "1" if profile.auto_download_content else "0",
            "selected_apps": profile.selected_apps_csv,
            "purpose": profile.purpose,
            "profile": str(profile.install_profile or "").strip().lower(),
            "disk_allocation_gb": str(profile.disk_allocation_gb),
            "network_cidr": profile.network_cidr,
            "internet_exposed": "1" if profile.exposure.internet_exposed else "0",
            "route_strategy": profile.exposure.route_strategy,
            "app_gateway_host": profile.exposure.gateway_host,
            "app_gateway_port": profile.exposure.gateway_port,
            "app_path_prefix": profile.exposure.normalized_app_path_prefix,
            "media_server_direct_host": profile.exposure.media_server_direct_host,
            "auth_provider": profile.exposure.auth_provider,
            "auth_middleware": profile.exposure.auth_middleware,
            "edge_router_provider": profile.exposure.edge_router_provider,
            "chaos_enabled": "1" if profile.chaos.enabled else "0",
            "chaos_duration_minutes": str(profile.chaos.duration_minutes),
            "chaos_interval_seconds": str(profile.chaos.interval_seconds),
            "chaos_actions": ",".join(profile.chaos.actions),
            "ingress_domain": ingress_domain,
        }


__all__ = ["DeployProfileDefaultsExtractor"]
