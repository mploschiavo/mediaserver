"""Maintainerr ``ServiceLifecycle``.

Maintainerr is a downstream consumer of upstream services' API keys
(Jellyfin, Sonarr, Radarr, Jellyseerr, Tautulli) — no key of its own.
Uses the shared ``NoApiKeyLifecycleBase``.
"""

from __future__ import annotations

from media_stack.adapters._lifecycle_base import NoApiKeyLifecycleBase
from media_stack.adapters.maintainerr.rules_wiring import (
    MaintainerrCollectionsWirer,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ServiceLifecycle,
)


# Stateless module-level singleton — the wirer is per-call parameterized
# by ctx (and, for ensure_rules_linked_to_arr, the injected
# configure_handler + job_context_factory). Constructor-injected probe
# coordinates (collections path / timeout) keep the magic-string surface
# in the wirer module rather than here.
_RULES_WIRER = MaintainerrCollectionsWirer()


class MaintainerrLifecycle(NoApiKeyLifecycleBase):
    service_id = "maintainerr"
    _default_health_path = "/app/maintainerr/api/settings"

    # --- Maintainerr rule-link wiring (ADR-0005 Phase 3) ------------
    #
    # Two methods delegate to ``MaintainerrCollectionsWirer`` in
    # ``rules_wiring.py``. The lifecycle owns the no-api-key shape
    # (inherited from ``NoApiKeyLifecycleBase``); the wirer owns the
    # collections-endpoint probe + the wide-handler delegation to
    # ``ensure_maintainerr_integrations``. The ensurer takes the
    # existing job handler + a ``JobContext`` factory because the
    # underlying integration flow is wide enough (test connections +
    # per-arr reconcile + rule sync) that re-implementing it inside
    # the wirer would duplicate ~200 lines of tested code (the
    # Jellyseerr ``ensure_arr_servers`` precedent).

    def probe_rules_linked_to_arr(
        self, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return _RULES_WIRER.probe(ctx)

    def ensure_rules_linked_to_arr(
        self, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        # Lazy imports keep the lifecycle module light at load time
        # (the runtime_ops handler pulls in arr-app discovery, the
        # rule-translation service, and Tautulli secret lookup) and
        # break the import cycle that would exist if the application
        # layer imported the lifecycle. Both imports go through the
        # ``services/`` shim layer (the same handler entry the legacy
        # job runner resolves from
        # ``contracts/services/maintainerr.yaml``) so the adapter
        # stays on the adapters/ → services/ side of the hexagon
        # ratchet — the application/ canonical module is reached
        # transitively via the shim, not by direct import here.
        from media_stack.services.apps.maintainerr.runtime_ops import (
            ensure_maintainerr_integrations,
        )
        from media_stack.services.jobs.framework import JobContext
        return _RULES_WIRER.ensure(
            ctx,
            configure_handler=ensure_maintainerr_integrations,
            job_context_factory=JobContext,
        )


_check: ServiceLifecycle = MaintainerrLifecycle()
del _check


# ADR-0010 Phase 7 — module-level Job-handler alias the
# ``maintainerr:ensure-rules-linked-to-arr`` contract entry references.
# The closure created by ``LifecycleHandlerAdapter.bind`` constructs
# a fresh ``MaintainerrLifecycle`` per call and adapts its
# ``Outcome`` return to the Job-handler dict shape.
from media_stack.domain.services.lifecycle_handler_adapter import (  # noqa: E402
    LifecycleHandlerAdapter,
)

ensure_rules_linked_to_arr = LifecycleHandlerAdapter.bind(
    MaintainerrLifecycle, "ensure_rules_linked_to_arr",
)


__all__ = [
    "MaintainerrLifecycle",
    "ensure_rules_linked_to_arr",
]
