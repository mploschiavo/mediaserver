"""Manual lifecycle-ensurer dispatch (ADR-0005 Phase 5b step 2).

Background
----------
The promise orchestrator already routes every unsatisfied promise's
``LifecycleEnsurer`` through a single entry point —
``infrastructure.promises.dispatcher.dispatch_ensurer``. ADR-0005
Phase 5b exposes that same entry point as a callable API surface so:

* The operator dashboard's "Run now" buttons can target an
  individual ensurer by ``(service, method)`` (Phase 5b step 3
  migrates the UI off ``POST /api/jobs/run/<legacy-name>``).
* Auto-heal can dispatch a recovery ensurer by
  ``(service, method)`` instead of opaquely calling
  ``action_trigger("ensure-X")`` (Phase 5b step 4).

Three callers (orchestrator-tick, operator, auto-heal) — one
underlying mechanism, no per-caller branching. That is the
architecture goal in ADR-0005.

This module hosts the service-side surface. The route binding lives
in ``api/routes/post_admin_ops.py`` (this is where every other admin
mutation lands; matching the bug-class memory note about CSRF +
admin gating).

Design
------
``LifecycleEnsurerInvoker`` is a small class with constructor-
injected collaborators:

* ``resolver`` — a ``LifecycleResolver`` (the same one the
  orchestrator hands to ``dispatch_ensurer``).
* ``registry_loader`` — callable returning the typed promise list.
  Used to enumerate the legitimate ``(service, method)`` set so
  unknown pairs map to ``404`` instead of accidentally constructing
  an arbitrary ``LifecycleEnsurer`` and passing it to dispatch.
* ``dispatch_fn`` — the dispatcher itself, injectable so unit tests
  swap a stub for the whole ``dispatch_ensurer`` body.
* ``clock`` — ``Callable[[], float]`` time source, injectable for
  deterministic tests.
* ``secrets_resolver`` — callable returning the secrets mapping for
  a given service id (matches the orchestrator's behaviour: it
  passes process-wide ``secrets`` into every dispatch).
* ``logger`` — module logger by default; tests pass a stub to
  assert audit-trail emissions.

Public surface: a single ``invoke`` method that takes a
``LifecycleEnsurerInvocation`` value object and returns
``(http_status, response_body)``. The value-object packaging means
the call site is ``invoker.invoke(LifecycleEnsurerInvocation(
service=..., method=..., source=...))`` — adding a new field
(e.g. correlation id) is an additive change, no positional-arg
shuffle through callers.

Source tagging
--------------
``invoke`` records the caller (``operator`` / ``auto-heal`` /
``orchestrator-tick``) into the response's ``source`` field, the
log line for the dispatch, and (transparently) into the
``OrchestrationContext.extra`` map via a thin wrapping resolver.
That last bit means audit-log / SSE writers downstream of the
ensurer can read the source without having to thread an extra arg
through every adapter. The orchestrator continues to pass
``source="orchestrator-tick"`` implicitly (it never goes through
this class), so existing dispatch callers see no change.

Outcome → HTTP mapping
----------------------
``Outcome`` is a value type with ``ok``/``transient`` flags. We map
it to a stable response envelope:

* ``ok=True``           → ``status=200``, body ``status="success"``
* ``ok=False, transient=True``  → ``status=200``, ``status="transient"``
* ``ok=False, transient=False`` → ``status=200``, ``status="permanent"``

The HTTP status is 200 for every successful dispatch. A non-200
response means "we couldn't run the ensurer" (404 unknown pair,
403 csrf/admin, 500 implementation bug). A 200 with
``status="permanent"`` means "we ran it; it permanently failed" —
the caller's bug, not ours, and the caller wants the structured
envelope, not a 5xx.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Callable, Iterable, Mapping

from media_stack.domain.services.identifiers import (
    EnsurerMethod,
    InvocationSource,
    ServiceId,
)
from media_stack.domain.services.lifecycle import (
    OrchestrationContext,
    Outcome,
)
from media_stack.domain.services.promises import (
    LifecycleEnsurer,
    Promise,
)
from media_stack.infrastructure.promises.dispatcher import (
    LifecycleResolver,
    dispatch_ensurer as _default_dispatch_ensurer,
)
from media_stack.infrastructure.promises.registry import load_registry


logger = logging.getLogger(__name__)


# Source-tag values. Operator dashboard → "operator"; auto-heal →
# "auto-heal"; the orchestrator tick path doesn't go through this
# class but the reserved value is here so route + audit emitters
# can compare against a single source-of-truth.
SOURCE_OPERATOR: InvocationSource = InvocationSource("operator")
SOURCE_AUTO_HEAL: InvocationSource = InvocationSource("auto-heal")
SOURCE_ORCHESTRATOR_TICK: InvocationSource = InvocationSource(
    "orchestrator-tick",
)
_KNOWN_SOURCES: frozenset[InvocationSource] = frozenset(
    {SOURCE_OPERATOR, SOURCE_AUTO_HEAL, SOURCE_ORCHESTRATOR_TICK},
)

# Response envelope status fields. Stable wire shape — the UI keys
# off these strings, so they belong to a single named site.
RESPONSE_STATUS_SUCCESS = "success"
RESPONSE_STATUS_TRANSIENT = "transient"
RESPONSE_STATUS_PERMANENT = "permanent"


DispatchFn = Callable[..., Outcome[Any]]
RegistryLoader = Callable[[], list[Promise]]
SecretsResolver = Callable[[ServiceId], Mapping[str, str]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class LifecycleEnsurerInvocation:
    """Value object bundling everything ``LifecycleEnsurerInvoker``
    needs to dispatch one manual lifecycle-ensurer call.

    Replaces a positional/kwarg-soup signature
    (``invoke(service, method, *, source, overrides)``) so adding a
    field (correlation id, requested-by user, override-budget, …)
    is an additive change at every call site instead of a fan-out
    rewrite. Frozen so the orchestrator can't mutate the request
    mid-dispatch.
    """

    service: ServiceId
    method: EnsurerMethod
    source: InvocationSource = SOURCE_OPERATOR
    overrides: Mapping[str, Any] = field(default_factory=dict)


class _SourceTaggingResolver:
    """Wraps a ``LifecycleResolver`` so every ``context_for`` call
    embeds the caller's source label into ``OrchestrationContext.extra``.

    Existence: ``LifecycleResolver`` is shared across orchestrator
    ticks AND this manual-invoke path. We don't want manual-invoke
    state to leak into orchestrator-tick contexts. Constructing this
    wrapper per-call keeps the tag scoped to a single dispatch.

    Surface mirrors only the methods ``dispatch_ensurer`` reads —
    ``resolve`` (for the lifecycle instance) and ``context_for``
    (for the context). Everything else falls through via
    ``__getattr__`` so future probe/ensurer dispatch additions
    keep working.
    """

    def __init__(
        self,
        inner: LifecycleResolver,
        *,
        source: InvocationSource,
    ) -> None:
        self._inner = inner
        self._source = source

    def resolve(self, service_id: ServiceId) -> Any:
        return self._inner.resolve(service_id)

    def context_for(
        self,
        service_id: ServiceId,
        *,
        secrets: Mapping[str, str] | None = None,
        now_fn: Any = None,
    ) -> OrchestrationContext:
        ctx = self._inner.context_for(
            service_id, secrets=secrets, now_fn=now_fn,
        )
        # ``OrchestrationContext`` is frozen; rebuild with the tag
        # merged into extra.
        merged_extra: dict[str, Any] = dict(ctx.extra)
        merged_extra["invocation_source"] = self._source
        return OrchestrationContext(
            service_id=ctx.service_id,
            config=ctx.config,
            secrets=ctx.secrets,
            now=ctx.now,
            is_cancelled=ctx.is_cancelled,
            dry_run=ctx.dry_run,
            extra=merged_extra,
        )

    def __getattr__(self, name: str) -> Any:
        # Pass-through for any non-overridden surface
        # (read_service_config, etc.). Keeps the wrapper transparent.
        return getattr(self._inner, name)


class LifecycleEnsurerInvoker:
    """Callable surface for manually dispatching a single
    ``LifecycleEnsurer`` against a known ``(service, method)`` pair.

    See module docstring for the why. Constructor-injects every
    collaborator so unit tests build a controlled dispatch path
    without touching the filesystem (registry YAML), the resolver
    cache, or the real ``dispatch_ensurer`` plumbing.
    """

    def __init__(
        self,
        *,
        resolver: LifecycleResolver | None = None,
        registry_loader: RegistryLoader | None = None,
        dispatch_fn: DispatchFn | None = None,
        clock: Clock | None = None,
        secrets_resolver: SecretsResolver | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self._resolver = resolver or LifecycleResolver()
        self._registry_loader = registry_loader or load_registry
        self._dispatch = dispatch_fn or _default_dispatch_ensurer
        self._clock = clock or time.time
        self._secrets_resolver = (
            secrets_resolver or self._empty_secrets
        )
        self._log = log or logger

    # --- public entry --------------------------------------------------

    def invoke(
        self,
        invocation: LifecycleEnsurerInvocation,
    ) -> tuple[int, dict[str, Any]]:
        """Manually dispatch a single lifecycle ensurer.

        Returns ``(http_status, response_body)``. See module-level
        ``Outcome → HTTP mapping`` for the envelope shape.
        """
        normalized_source = self._normalize_source(invocation.source)
        if not self._is_known_pair(invocation.service, invocation.method):
            self._log.warning(
                "lifecycle-ensurer manual invoke: unknown pair "
                "service=%r method=%r source=%r",
                invocation.service,
                invocation.method,
                normalized_source,
            )
            return HTTPStatus.NOT_FOUND, {
                "error": "unknown ensurer",
                "service": invocation.service,
                "method": invocation.method,
            }
        spec = LifecycleEnsurer(
            service=invocation.service, method=invocation.method,
        )
        wrapped = _SourceTaggingResolver(
            self._resolver, source=normalized_source,
        )
        secrets = self._secrets_resolver(invocation.service) or {}
        now = self._clock()
        self._log.info(
            "lifecycle-ensurer manual invoke: service=%r method=%r "
            "source=%r overrides_keys=%s",
            invocation.service,
            invocation.method,
            normalized_source,
            sorted(invocation.overrides.keys()),
        )
        outcome = self._dispatch(
            spec,
            resolver=wrapped,
            now=now,
            secrets=secrets,
        )
        body = self._build_response(outcome, normalized_source)
        return HTTPStatus.OK, body

    # --- internals -----------------------------------------------------

    def _is_known_pair(
        self, service: ServiceId, method: EnsurerMethod,
    ) -> bool:
        try:
            return (service, method) in self._known_pairs()
        except Exception as exc:  # noqa: BLE001
            # If the registry can't be loaded the controller is
            # already broken in deeper ways; the safe answer here
            # is "treat as unknown" so the caller gets a 404 rather
            # than a 500.
            self._log.error(
                "lifecycle-ensurer registry load failed; "
                "treating pair as unknown: %s", exc,
            )
            return False

    def _known_pairs(self) -> set[tuple[ServiceId, EnsurerMethod]]:
        promises = self._registry_loader() or []
        return {
            (
                ServiceId(p.ensurer.service),
                EnsurerMethod(p.ensurer.method),
            )
            for p in self._iter_lifecycle_promises(promises)
        }

    def _normalize_source(
        self, source: InvocationSource | str | None,
    ) -> InvocationSource:
        if source and source in _KNOWN_SOURCES:
            return InvocationSource(source)
        # Unknown / missing source defaults to operator. We never
        # 400 on an unrecognized source because source is purely
        # observability-side; misclassifying it doesn't change
        # business behaviour, just audit fidelity.
        if source:
            self._log.debug(
                "lifecycle-ensurer invoke: unrecognized source %r; "
                "falling back to %r", source, SOURCE_OPERATOR,
            )
        return SOURCE_OPERATOR

    def _build_response(
        self, outcome: Outcome[Any], source: InvocationSource,
    ) -> dict[str, Any]:
        if outcome.ok:
            envelope_status = RESPONSE_STATUS_SUCCESS
            message = "lifecycle ensurer succeeded"
        elif outcome.transient:
            envelope_status = RESPONSE_STATUS_TRANSIENT
            message = outcome.error or "transient failure"
        else:
            envelope_status = RESPONSE_STATUS_PERMANENT
            message = outcome.error or "permanent failure"
        return {
            "status": envelope_status,
            "message": message,
            "source": source,
            "evidence": dict(outcome.evidence),
            "attempts": int(outcome.attempts),
            "elapsed_seconds": float(outcome.elapsed_seconds),
        }

    def _iter_lifecycle_promises(
        self, promises: Iterable[Promise],
    ) -> Iterable[Promise]:
        """Filter a promise list down to those whose ensurer is a
        ``LifecycleEnsurer`` (not a legacy ``JobEnsurer``). Bound
        as an instance method (rather than ``@staticmethod``) per
        the OO-discipline ratchet — keeps the class the single
        public surface and leaves room for subclasses to widen
        the predicate (e.g. ADR-0005 Phase 5c JobEnsurer cutover)
        without changing call sites."""
        for p in promises:
            if isinstance(p.ensurer, LifecycleEnsurer):
                yield p

    def _empty_secrets(
        self, _service: ServiceId,
    ) -> Mapping[str, str]:
        """Default secrets resolver — empty.

        The orchestrator passes a populated secrets mapping merged
        from env + secrets-file mounts, but the synchronous
        operator/auto-heal entry point doesn't have a request-scoped
        secrets context yet. Lifecycles that need credentials read
        from env directly (the
        ``OrchestrationContext.config.api_key_env`` lookup), so an
        empty default is correct for the migration. Step 4
        (auto-heal cutover) wires a real resolver here.
        """
        return {}


__all__ = [
    "LifecycleEnsurerInvocation",
    "LifecycleEnsurerInvoker",
    "RESPONSE_STATUS_PERMANENT",
    "RESPONSE_STATUS_SUCCESS",
    "RESPONSE_STATUS_TRANSIENT",
    "SOURCE_AUTO_HEAL",
    "SOURCE_OPERATOR",
    "SOURCE_ORCHESTRATOR_TICK",
]
