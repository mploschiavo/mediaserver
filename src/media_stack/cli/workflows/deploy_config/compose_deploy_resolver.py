"""ComposeDeployResolver — Strategy for compose-platform-specific resolution.

Two things the compose deploy adapter needs that the k8s adapter
doesn't:

* The list of env vars the compose-side preflight pipeline should
  pass through to the bootstrap container. This includes the
  stack-admin credentials plus any service-specific secret env-var
  names declared under ``adapter_hooks.bootstrap_job.secret_priming_targets``.
* The ordered list of ``compose_preflight_handler`` specs to invoke
  before bootstrap runs (the routing/UrlBase/SSO-bypass chain
  ADR-0015 Phase 3 was investigating).

Strategy pattern: this class is the compose-specific resolution
strategy. K8s doesn't go through here — it has its own preflight
chain in ``container_preflight_handlers``. Splitting the compose
concern out into its own resolver makes the platform asymmetry
explicit in the type system (no leaky "is this compose? then read
that key" branching elsewhere).
"""

from __future__ import annotations

from media_stack.cli.commands.deploy_stack_errors import DeployError
from media_stack.cli.workflows.deploy_config.bootstrap_config_loader import (
    BootstrapConfigLoader,
)
from media_stack.cli.workflows.deploy_hook_config_resolver import (
    DeployHookConfigResolverService,
)


class ComposeDeployResolver:
    """Strategy: compose-specific deploy hooks (env passthrough + preflights)."""

    def __init__(
        self,
        loader: BootstrapConfigLoader,
        *,
        hook_resolver: DeployHookConfigResolverService | None = None,
    ) -> None:
        self._loader = loader
        self._hook_resolver = hook_resolver or DeployHookConfigResolverService()

    def passthrough_env_vars(self) -> tuple[str, ...]:
        """Env vars the compose preflight passes to the bootstrap container.

        Always includes ``STACK_ADMIN_USERNAME`` and ``STACK_ADMIN_PASSWORD``
        (the universal credential pair). Adds any service-specific
        env-var names declared in
        ``adapter_hooks.bootstrap_job.secret_priming_targets`` (e.g.
        ``QBITTORRENT_PASSWORD`` for the qB credential rotation).
        Deduped in declaration order so the resulting list is
        deterministic across reruns.
        """
        env_vars: list[str] = ["STACK_ADMIN_USERNAME", "STACK_ADMIN_PASSWORD"]
        hooks = self._loader.bootstrap_job_hooks()
        secret_targets = hooks.get("secret_priming_targets")
        if isinstance(secret_targets, dict):
            for spec in secret_targets.values():
                if not isinstance(spec, dict):
                    continue
                name = str(spec.get("env_var") or "").strip()
                if name:
                    env_vars.append(name)
        deduped: list[str] = []
        seen: set[str] = set()
        for raw_name in env_vars:
            name = str(raw_name or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        return tuple(deduped)

    def preflight_handlers(self) -> tuple[str, ...]:
        """Compose preflight handler specs to run before bootstrap.

        Aggregates two sources via :class:`DeployHookConfigResolverService`:

        * Every contract YAML's ``plugin.compose_preflight_handler``
          field (the per-service preflight pattern this ADR's
          Phase 3 bug parade investigated).
        * Any extra handlers declared in
          ``adapter_hooks.bootstrap_job.compose_preflight_handlers``.

        ``ValueError`` from the resolver gets translated to
        :class:`DeployError` because deploy-flow callers expect the
        deploy-namespaced exception type.
        """
        try:
            return self._hook_resolver.compose_preflight_handlers(
                self._loader.bootstrap_job_hooks(),
            )
        except ValueError as exc:
            raise DeployError(str(exc)) from exc


__all__ = ["ComposeDeployResolver"]
