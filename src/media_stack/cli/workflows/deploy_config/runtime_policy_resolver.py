"""RuntimePolicyResolver — Strategy for the bootstrap-job runtime policy.

The "runtime config policy" is a controller-side hook the operator's
profile names: a function (``module.path:Symbol``) the bootstrap
job calls with a runtime-params dict to compute the per-deploy
runtime config (e.g. whether to auto-download content, what auth
mode to seed, what the gateway host is). It's the deploy-time
contract between the operator's profile YAML and the controller's
"figure out what to do at runtime" logic.

Strategy pattern: two methods — one names the handler, one builds
the params dict the handler consumes. Both pull from the operator's
:class:`DeployStackConfig` plus the active edge-routing strategy
(for ``edge_router_provider``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_stack.cli.commands.deploy_stack_errors import DeployError
from media_stack.cli.workflows.deploy_config.bootstrap_config_loader import (
    BootstrapConfigLoader,
)
from media_stack.cli.workflows.deploy_config.edge_routing_resolver import (
    EdgeRoutingResolver,
)

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )


_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


class RuntimePolicyResolver:
    """Strategy: bootstrap-job runtime policy handler + params dict."""

    def __init__(
        self,
        cfg: "DeployStackConfig",
        loader: BootstrapConfigLoader,
        edge_routing: EdgeRoutingResolver,
    ) -> None:
        self._cfg = cfg
        self._loader = loader
        self._edge_routing = edge_routing

    def handler_spec(self) -> str:
        """The fully-qualified handler symbol the bootstrap job calls.

        Read from ``adapter_hooks.bootstrap_job.runtime_config_policy_handler``.
        Must be ``module.path:Symbol`` shape — anything else is a
        contract violation we surface as :class:`DeployError`.
        Returns empty string if not set (the deploy adapter then
        decides whether that's acceptable for the platform).
        """
        hooks = self._loader.bootstrap_job_hooks()
        spec = str(hooks.get("runtime_config_policy_handler") or "").strip()
        if spec and ":" not in spec:
            raise DeployError(
                "adapter_hooks.bootstrap_job.runtime_config_policy_handler "
                "must be module.path:Symbol",
            )
        return spec

    def params(self) -> dict[str, object]:
        """Build the params dict the runtime-config-policy handler consumes.

        The shape is the operator's CLI/env answers normalised:
        booleans converted from their string form, the active
        edge_router_provider (resolved against override + hook),
        and the rest of the operator-tunable fields verbatim from
        the :class:`DeployStackConfig`.
        """
        return {
            "selected_apps_csv": self._cfg.selected_apps,
            "preconfigure_api_keys": self._is_truthy(self._cfg.preconfigure_api_keys),
            "auto_download_content": self._is_truthy(self._cfg.auto_download_content),
            "internet_exposed": self._is_truthy(self._cfg.internet_exposed),
            "route_strategy": self._cfg.route_strategy,
            "auth_provider": self._cfg.auth_provider,
            "auth_middleware": self._cfg.auth_middleware,
            "edge_router_provider": self._edge_routing.router_provider(),
            "ingress_domain": self._cfg.ingress_domain,
            "app_gateway_host": self._cfg.app_gateway_host,
            "app_gateway_port": self._cfg.app_gateway_port,
            "app_path_prefix": self._cfg.app_path_prefix,
            "media_server_direct_host": self._cfg.media_server_direct_host,
        }

    def _is_truthy(self, value: str) -> bool:
        return str(value or "").strip().lower() in _TRUTHY_VALUES


__all__ = ["RuntimePolicyResolver"]
