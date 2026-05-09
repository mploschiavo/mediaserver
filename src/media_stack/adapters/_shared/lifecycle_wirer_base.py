"""Shared base for ADR-0005 Phase 3 wirer classes.

Each ``adapters/<svc>/<topic>_wiring.py`` defines a wirer that
ports a legacy ``ensure-*`` job handler to the orchestrator's
lifecycle-method dispatch. After 8 wirers shipped (notifier,
indexer-pipeline, runtime-defaults, seed-series, bazarr-config,
jellyseerr-config, qbit-categories, maintainerr-rules), the
``duplicate-code`` ratchet flagged the same shape repeating in
each — the urllib HTTPError / URLError classification pattern,
the ``ctx.secrets → os.environ`` env-var fallback, the ProbeResult
/ Outcome construction helpers.

This base class collects those into one place. Wirer signatures
are intentionally NOT abstracted here — the Servarr family wirers
are parameterized by ``service_id`` while single-service wirers
(qBit, Maintainerr) only take ``ctx``. Constraining the
``probe`` / ``ensure`` signatures would force an awkward
common-denominator shape that obscures each wirer's actual
contract.

Inheriting wirers stay class-based (per OO discipline rule) and
constructor-injected — the base adds shared helpers, doesn't
take over construction or dispatch.
"""

from __future__ import annotations

import os
import urllib.error
from typing import Any

from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)

# Module-top import of the Phase 7 adapter — keeps
# ``bind_method_as_job`` free of a function-level import (which
# would tick the CIRCULAR_IMPORT_RISK_RATCHET). The adapter module
# imports only from ``domain.services``, so no cycle exists in the
# static graph.
from media_stack.domain.services.lifecycle_handler_adapter import (
    LifecycleHandlerAdapter,
)


class LifecycleWirerBase:
    """Common helpers for ADR-0005 Phase 3 wirer classes.

    Subclasses define their own ``__init__`` (constructor-injected
    deps), ``probe(...)`` and ``ensure(...)`` methods (signatures
    vary), and use the helpers below for the patterns that ARE
    common across every wirer.
    """

    # --- ProbeResult shortcuts --------------------------------------

    def _probe_ok(
        self,
        ctx: OrchestrationContext,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> ProbeResult:
        return ProbeResult.ok(
            message,
            evidence=evidence or {},
            evaluated_at=ctx.now(),
        )

    def _probe_failed(
        self,
        ctx: OrchestrationContext,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> ProbeResult:
        return ProbeResult.failed(
            message,
            evidence=evidence or {},
            evaluated_at=ctx.now(),
        )

    def _probe_unknown(
        self,
        ctx: OrchestrationContext,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> ProbeResult:
        return ProbeResult.unknown(
            message,
            evidence=evidence or {},
            evaluated_at=ctx.now(),
        )

    # --- Outcome shortcuts -----------------------------------------

    def _outcome_success(
        self,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> Outcome[None]:
        return Outcome.success(None, evidence=evidence or {})

    def _outcome_transient(
        self,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> Outcome[None]:
        return Outcome.failure(
            message,
            transient=True,
            evidence=evidence or {},
        )

    def _outcome_permanent(
        self,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> Outcome[None]:
        return Outcome.failure(
            message,
            transient=False,
            evidence=evidence or {},
        )

    # --- HTTP error → Outcome classifier ---------------------------

    def _classify_http_outcome(
        self,
        exc: BaseException,
        *,
        url: str,
    ) -> Outcome[None]:
        """Map a urllib exception to the canonical Outcome shape.

        4xx / 5xx (HTTPError) → ``permanent`` — the *arr's API
        rejected the payload; retrying won't help.

        URLError / OSError / TimeoutError → ``transient`` —
        network-level problem; orchestrator's next tick may
        succeed.

        Anything else propagates — the wirer's caller is
        responsible for narrowing the except clauses to known
        exception types.
        """
        if isinstance(exc, urllib.error.HTTPError):
            return self._outcome_permanent(
                f"HTTP {exc.code} from {url}",
                evidence={"http_status": exc.code, "url": url},
            )
        if isinstance(
            exc, (urllib.error.URLError, OSError, TimeoutError),
        ):
            return self._outcome_transient(
                f"unreachable at {url}: {exc}",
                evidence={"url": url, "error": str(exc)},
            )
        # Unexpected exception type — let it propagate. The wirer's
        # except clause should narrow to known types; reaching here
        # means a bug.
        raise exc

    # --- Outcome → Job-handler dict adapter -----------------------
    #
    # ADR-0010 Phase 7: each wirer's ``ensure(...)`` becomes the
    # body of a single-step Job whose contract entry has
    # ``satisfies: [<promise-id>]``. JobRunner expects ``(ctx) ->
    # dict`` from a handler; ``ensure(...)`` returns ``Outcome``.
    # This adapter lives on the shared base because it's the only
    # place that knows BOTH halves (Wirer + Outcome shape).
    # Lifecycle dispatch (``_ensure_lifecycle``) is retired in the
    # same phase; this helper replaces the indirection layer.

    @classmethod
    def bind_method_as_job(
        cls,
        method_name: str = "ensure",
        *,
        service_id: str | None = None,
    ) -> Any:
        """Return a Job-handler closure bound to a wirer method.

        Convenience wrapper around
        ``LifecycleHandlerAdapter.bind`` for wirer subclasses —
        threads the calling subclass into the adapter, plus the
        ``service_id`` parameter for Servarr-family wirers whose
        ensure signature is ``(service_id, ctx)``.

        Module-level aliases bind once at import:

            ensure_categories = CategoriesWirer.bind_method_as_job()
            ensure_oidc = JellyseerrConfigWirer.bind_method_as_job("ensure_oidc")
            ensure_radarr_api_key = ApiKeyWirer.bind_method_as_job(service_id="radarr")
        """
        if service_id is None:
            return LifecycleHandlerAdapter.bind(cls, method_name)

        # Servarr-family wirers take ``(service_id, ctx)`` rather
        # than ``(ctx)`` — wrap so the adapter's ``(ctx) -> dict``
        # shape still holds.
        def handler(ctx: OrchestrationContext) -> dict[str, Any]:
            method = getattr(cls(), method_name)
            outcome = method(service_id, ctx)
            return LifecycleHandlerAdapter.outcome_to_dict(outcome)

        return handler

    # --- Secret-discovery helper -----------------------------------

    def _discover_secret(
        self,
        ctx: OrchestrationContext,
        env_var: str,
    ) -> str:
        """``ctx.secrets`` first, ``os.environ`` fallback. The
        established pattern across every wirer + ServarrLifecycle.
        Returns ``""`` (empty string) when neither has the key —
        callers check for emptiness as the "not yet discoverable"
        signal."""
        return (
            (ctx.secrets.get(env_var) or "").strip()
            or os.environ.get(env_var, "").strip()
        )


__all__ = ["LifecycleWirerBase"]
