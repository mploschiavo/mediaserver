"""DeployConfigService — Facade composing 6 single-responsibility resolvers.

Facade pattern (Gang-of-Four): a unified surface that hides the
sub-system of resolvers behind one object. The call site
(:class:`DeployStackRunner` in ``cli/commands/deploy_stack_main.py``)
has been talking to ``self.config_service.xxx()`` since Phase 3;
Phase 3b keeps that exact surface but moves the actual logic onto
6 SRP classes the facade delegates to.

The facade itself **owns no logic** — every public method is a
one-liner that calls into a resolver. If a method on the facade
starts doing real work, that's the symptom: extract a new resolver
or move the method onto an existing one.

Pre-Phase-3b ``DeployConfigService`` was a 22-method god class
covering 6 distinct responsibilities (the audit's original
finding). The facade now composes:

* :class:`BootstrapConfigLoader` (Repository) — the JSON + cache.
* :class:`EdgeRoutingResolver` (Strategy) — Envoy/Traefik config.
* :class:`AuthProviderResolver` (Strategy) — auth middleware + valid set.
* :class:`ProfileCatalogValidator` (Validator) — catalog allow-lists.
* :class:`RuntimePolicyResolver` (Strategy) — bootstrap-job policy.
* :class:`ComposeDeployResolver` (Strategy) — compose-specific bits.

Each resolver is constructor-injected (in dependency order) so a
test fixture or future refactor can replace any single resolver
without touching the others. The facade's constructor builds the
default chain; the operator never sees the wiring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_stack.cli.workflows.deploy_config.auth_provider_resolver import (
    AuthProviderResolver,
)
from media_stack.cli.workflows.deploy_config.bootstrap_config_loader import (
    BootstrapConfigLoader,
)
from media_stack.cli.workflows.deploy_config.compose_deploy_resolver import (
    ComposeDeployResolver,
)
from media_stack.cli.workflows.deploy_config.edge_routing_resolver import (
    EdgeRoutingResolver,
)
from media_stack.cli.workflows.deploy_config.profile_catalog_validator import (
    ProfileCatalogValidator,
)
from media_stack.cli.workflows.deploy_config.runtime_policy_resolver import (
    RuntimePolicyResolver,
)
from media_stack.cli.workflows.deploy_hook_config_resolver import (
    DeployHookConfigResolverService,
)

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )


class DeployConfigService:
    """Facade: composes 6 SRP resolvers behind one call-site surface.

    Public method names match what :class:`DeployStackRunner`
    already calls (the pre-Phase-3b surface). All work delegates
    to the composed resolvers — this class owns no logic.
    """

    def __init__(
        self,
        cfg: "DeployStackConfig",
        *,
        hook_resolver: DeployHookConfigResolverService | None = None,
    ) -> None:
        # Build the resolver chain in dependency order. Constructor
        # injection means any single resolver can be swapped for a
        # test double by re-wiring this facade — no monkey-patching
        # of module-level globals required.
        self._hook_resolver = hook_resolver or DeployHookConfigResolverService()
        self._loader = BootstrapConfigLoader(
            cfg, hook_resolver=self._hook_resolver,
        )
        self._edge_routing = EdgeRoutingResolver(cfg, self._loader)
        self._auth = AuthProviderResolver(self._loader)
        self._catalog = ProfileCatalogValidator(self._edge_routing)
        self._runtime_policy = RuntimePolicyResolver(
            cfg, self._loader, self._edge_routing,
        )
        self._compose = ComposeDeployResolver(
            self._loader, hook_resolver=self._hook_resolver,
        )

    # -- Loader surface ----------------------------------------------------

    def resolved_bootstrap_config(self) -> dict[str, object]:
        return self._loader.resolved()

    def adapter_hook_subkey(self, child_key: str) -> dict[str, object]:
        return self._loader.adapter_hook_subkey(child_key)

    def bootstrap_job_hooks(self) -> dict[str, object]:
        return self._loader.bootstrap_job_hooks()

    def edge_hooks(self) -> dict[str, object]:
        return self._loader.edge_hooks()

    def rebuild_profile_actions(
        self,
    ) -> tuple[
        dict[str, tuple[str, ...]],
        dict[str, tuple[str, ...]],
        dict[str, str],
        dict[str, tuple[str, ...]],
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        return self._loader.profile_actions()

    # -- Edge routing surface ---------------------------------------------

    def edge_router_provider(self) -> str:
        return self._edge_routing.router_provider()

    def edge_router_service_names(self) -> tuple[str, ...]:
        return self._edge_routing.router_service_names()

    def edge_path_prefix_redirect_service_names(self) -> tuple[str, ...]:
        return self._edge_routing.path_prefix_redirect_service_names()

    def edge_path_prefix_preserve_service_names(self) -> tuple[str, ...]:
        return self._edge_routing.path_prefix_preserve_service_names()

    def edge_compose_provider_specs(self) -> dict[str, dict[str, str]]:
        return self._edge_routing.compose_provider_specs()

    def ingress_class_priority(self) -> tuple[str, ...]:
        return self._edge_routing.ingress_class_priority()

    def media_server_service_names(self) -> tuple[str, ...]:
        return self._edge_routing.media_server_service_names()

    # -- Auth provider surface --------------------------------------------

    def auth_provider_middleware_defaults(self) -> dict[str, str]:
        return self._auth.middleware_defaults()

    def valid_auth_providers(self) -> tuple[str, ...]:
        return self._auth.valid_providers()

    # -- Catalog validation surface ---------------------------------------

    def valid_route_strategies(self) -> tuple[str, ...]:
        return self._catalog.valid_route_strategies()

    def valid_edge_router_providers(self) -> tuple[str, ...]:
        return self._catalog.valid_edge_router_providers()

    # -- Runtime policy surface -------------------------------------------

    def runtime_config_policy_handler_spec(self) -> str:
        return self._runtime_policy.handler_spec()

    def runtime_config_policy_params(self) -> dict[str, object]:
        return self._runtime_policy.params()

    # -- Compose deploy surface -------------------------------------------

    def compose_passthrough_env_vars(self) -> tuple[str, ...]:
        return self._compose.passthrough_env_vars()

    def compose_preflight_handlers(self) -> tuple[str, ...]:
        return self._compose.preflight_handlers()


__all__ = ["DeployConfigService"]
