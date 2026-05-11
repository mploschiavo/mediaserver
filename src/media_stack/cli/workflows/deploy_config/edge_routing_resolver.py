"""EdgeRoutingResolver — Strategy for resolving Envoy/Traefik routing config.

Owns every question the deploy needs to answer about how requests
get from the gateway to the right service:

* Which router are we using? (envoy / traefik / nginx, from
  operator override or profile-driven hook)
* Which services does that router need to know about? (the
  ``router_service_names`` set — depends on which provider, which
  service registry, and any profile overrides)
* Which paths should redirect (e.g. ``/app/sonarr`` →
  ``/app/sonarr/``) and which should preserve the path prefix
  through to the upstream app?
* What compose-label/spec needs to be emitted for each provider?
* What ingress-class priority should the k8s overlay try, in order?
* Which service IDs are media servers (Jellyfin / Plex / Emby)
  for the routing layer to special-case?

Strategy pattern: the resolver consults three sources in priority
order:

1. The operator's CLI / env override (``self._cfg.edge_router_provider``).
2. The profile-driven ``adapter_hooks.edge`` config in the
   bootstrap JSON.
3. The per-service YAML contract registry (``web_ui`` /
   ``preserve_path_prefix`` flags) as a fallback when the hook
   config doesn't enumerate.

Each public method picks the right source for its concern. The
private ``_provider_hook_values`` helper centralises the
"per-provider override map + fallback list" dance that several
methods share.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_stack.core.edge.provider_registry import (
    compose_label_specs_by_provider,
    router_service_names_by_provider,
)
from media_stack.core.logging_utils import log_swallowed
from media_stack.cli.workflows.deploy_config.bootstrap_config_loader import (
    BootstrapConfigLoader,
)

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )


class EdgeRoutingResolver:
    """Strategy: edge-routing config from cfg override, hooks, registry."""

    def __init__(
        self,
        cfg: "DeployStackConfig",
        loader: BootstrapConfigLoader,
    ) -> None:
        self._cfg = cfg
        self._loader = loader

    def router_provider(self) -> str:
        """Resolve the active edge router provider.

        Priority: operator's CLI/env override (``cfg.edge_router_provider``)
        beats the hook config (``adapter_hooks.edge.router_provider``).
        Returns empty string if neither names a provider — the
        :class:`ProfileCatalogValidator` then errors out cleanly.
        """
        explicit = str(self._cfg.edge_router_provider or "").strip().lower()
        if explicit:
            return explicit
        hooks = self._loader.edge_hooks()
        return str(hooks.get("router_provider") or "").strip().lower()

    def router_service_names(self) -> tuple[str, ...]:
        """Service IDs the edge router needs to know exist.

        Compose-label / k8s-ingress generators iterate this set to
        emit per-service routing rules. Combines the
        registry-default service-name list for the selected
        provider with any profile-driven override.
        """
        provider_defaults = router_service_names_by_provider()
        provider = self.router_provider()
        out: list[str] = []
        seen: set[str] = set()
        for item in tuple(provider_defaults.get(provider) or ()):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        for item in self._provider_hook_values(
            by_provider_key="router_service_names_by_provider",
            fallback_key="router_service_names",
        ):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def path_prefix_redirect_service_names(self) -> tuple[str, ...]:
        """Service IDs whose path prefix should redirect to the
        trailing-slash form (e.g. ``/app/sonarr`` →
        ``/app/sonarr/``). Falls back to the per-service registry's
        ``web_ui=True`` set when the hook config doesn't enumerate.
        """
        out: list[str] = []
        seen: set[str] = set()
        for item in self._provider_hook_values(
            by_provider_key="path_prefix_redirect_service_names_by_provider",
            fallback_key="path_prefix_redirect_service_names",
        ):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        if out:
            return tuple(out)
        try:
            from media_stack.core.service_registry.registry import (
                get_web_ui_services,
            )
            svcs = get_web_ui_services()
            if svcs:
                return tuple(s.id for s in svcs)
        except ImportError as exc:
            log_swallowed(exc)
        return ()

    def path_prefix_preserve_service_names(self) -> tuple[str, ...]:
        """Service IDs whose path prefix should be passed through
        to the upstream app (the ``preserve_path_prefix=True``
        contract). Falls back to the registry's flag set when the
        hook config doesn't enumerate.
        """
        out: list[str] = []
        seen: set[str] = set()
        for item in self._provider_hook_values(
            by_provider_key="path_prefix_preserve_service_names_by_provider",
            fallback_key="path_prefix_preserve_service_names",
        ):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        if out:
            return tuple(out)
        try:
            from media_stack.core.service_registry.registry import (
                get_preserve_path_prefix_services,
            )
            svcs = get_preserve_path_prefix_services()
            if svcs:
                return tuple(s.id for s in svcs)
        except ImportError as exc:
            log_swallowed(exc)
        return ()

    def compose_provider_specs(self) -> dict[str, dict[str, str]]:
        """The per-provider Compose-label spec map.

        Starts from the registry's built-in defaults
        (:func:`compose_label_specs_by_provider`) and merges in any
        operator overrides from ``adapter_hooks.edge.compose_provider_specs``.
        Each spec is a flat ``{label_key: label_value}`` dict the
        Compose adapter emits onto each service.
        """
        out: dict[str, dict[str, str]] = {
            provider: dict(spec)
            for provider, spec in compose_label_specs_by_provider().items()
        }
        hooks = self._loader.edge_hooks()
        raw = hooks.get("compose_provider_specs")
        if isinstance(raw, dict):
            for raw_provider, raw_spec in raw.items():
                provider = str(raw_provider or "").strip().lower()
                if not provider or not isinstance(raw_spec, dict):
                    continue
                merged_spec = dict(out.get(provider) or {})
                for raw_key, raw_value in raw_spec.items():
                    key = str(raw_key or "").strip()
                    value = str(raw_value or "").strip()
                    if key and value:
                        merged_spec[key] = value
                out[provider] = merged_spec
        return out

    def ingress_class_priority(self) -> tuple[str, ...]:
        """Ordered ingress-class candidates for k8s deploys.

        Dedup-and-trim the operator's preferred order from
        ``adapter_hooks.edge.ingress_class_priority``. The k8s
        deploy adapter walks this list and uses the first class
        that exists in the cluster.
        """
        hooks = self._loader.edge_hooks()
        raw = hooks.get("ingress_class_priority")
        if not isinstance(raw, list):
            return ()
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def media_server_service_names(self) -> tuple[str, ...]:
        """Service IDs treated as media servers by the routing layer.

        Priority: 1. adapter_hooks.edge override → 2. profile
        ``routing.media_server_service_names`` → 3. derive the
        single name from ``technology_bindings.media_server``.
        """
        hooks = self._loader.edge_hooks()
        raw = hooks.get("media_server_service_names")
        out: list[str] = []
        seen: set[str] = set()
        if isinstance(raw, list):
            for item in raw:
                token = str(item or "").strip().lower()
                if not token or token in seen:
                    continue
                seen.add(token)
                out.append(token)
        if out:
            return tuple(out)
        try:
            from media_stack.core.controller_profile import (
                load_bootstrap_profile,
            )
            profile = load_bootstrap_profile()
            routing = profile.get("routing") or {}
            raw_profile = routing.get("media_server_service_names")
            if isinstance(raw_profile, list) and raw_profile:
                return tuple(
                    str(s).strip().lower()
                    for s in raw_profile
                    if str(s).strip()
                )
        except (ImportError, ValueError, OSError) as exc:
            log_swallowed(exc)
        cfg = self._loader.resolved()
        technology_bindings = cfg.get("technology_bindings")
        if isinstance(technology_bindings, dict):
            token = str(technology_bindings.get("media_server") or "").strip().lower()
            if token:
                return (token,)
        return ()

    def _provider_hook_values(
        self,
        *,
        by_provider_key: str,
        fallback_key: str,
    ) -> tuple[str, ...]:
        """Common dance: per-provider override map + flat fallback.

        Many edge-routing methods need to ask "is there a list for
        the active provider? if not, is there a flat fallback list?"
        This helper does that lookup. Private because the public
        methods know which key pair applies to their concern.
        """
        hooks = self._loader.edge_hooks()
        provider = self.router_provider()
        values: list[str] = []
        seen: set[str] = set()

        raw_by_provider = hooks.get(by_provider_key)
        selected_from_provider_map = False
        if isinstance(raw_by_provider, dict) and provider:
            provider_values = raw_by_provider.get(provider)
            if isinstance(provider_values, list):
                selected_from_provider_map = True
                for item in provider_values:
                    token = str(item or "").strip().lower()
                    if not token or token in seen:
                        continue
                    seen.add(token)
                    values.append(token)

        if not selected_from_provider_map:
            raw_fallback = hooks.get(fallback_key)
            if isinstance(raw_fallback, list):
                for item in raw_fallback:
                    token = str(item or "").strip().lower()
                    if not token or token in seen:
                        continue
                    seen.add(token)
                    values.append(token)

        return tuple(values)


__all__ = ["EdgeRoutingResolver"]
