"""Adapter that converts a lifecycle method (returns ``Outcome``) into
a Job-framework handler (returns ``dict``).

ADR-0010 Phase 7 retires the lifecycle-dispatch indirection in
``infrastructure.promises.dispatcher`` (``_ensure_lifecycle`` +
``LifecycleResolver``). Each promise's ``ensured_by`` flips from
``type: lifecycle`` to ``type: job``; the Job's ``handler:`` field
references a module-level callable produced by this adapter.

Lives in ``domain/services/`` (not ``application/jobs/``) because
adapter modules import it — the per-service ``Lifecycle`` classes
plus the shared wirer base both bind their ensure methods as Job
handlers at module scope. Adapters may depend on domain types but
not on application code (per the hexagonal-layering ratchet); this
adapter only operates on the ``Outcome`` domain value object and
produces a plain ``dict``, so it has no application-layer concerns.

Two surfaces:

* ``bind`` — returns a closure ``(ctx) -> dict`` bound to a
  specific class + method (and optional constructor kwargs for
  family-style classes parameterized by ``service_id``).
* ``outcome_to_dict`` — the conversion itself, exposed so wirers
  whose ``ensure`` is called by hand-written code can reuse the
  same shape.
"""

from __future__ import annotations

from typing import Any, Callable

from media_stack.domain.services import OrchestrationContext, Outcome


class LifecycleHandlerAdapter:
    """Builds Job-framework handlers from lifecycle/wirer methods.

    The Job framework expects a callable ``(ctx) -> dict``.
    Lifecycle methods return ``Outcome[T]``. The adapter bridges
    the two and lives in one place so changes to the result-dict
    shape (e.g., adding a ``transient`` field for cooldown) flow
    through every binding without touching adapter call sites.
    """

    @classmethod
    def bind(
        cls,
        target_class: type,
        method_name: str = "ensure",
        **constructor_kwargs: Any,
    ) -> Callable[[OrchestrationContext], dict[str, Any]]:
        """Return a Job-handler closure bound to a class + method.

        The closure constructs a fresh instance per invocation
        (lifecycle/wirer instances are stateless across calls in
        this codebase — same convention as the legacy
        ``_ensure_lifecycle`` resolver). ``constructor_kwargs``
        threads ``service_id`` (or any future per-instance config)
        into the constructor for family-style classes.

        Module-level aliases bind once at import:

            ensure_config_wiring = LifecycleHandlerAdapter.bind(
                BazarrLifecycle, "ensure_config_wiring",
            )

            radarr_ensure_indexers = LifecycleHandlerAdapter.bind(
                ServarrLifecycle, "ensure_indexers",
                service_id="radarr",
            )
        """
        def handler(ctx: OrchestrationContext) -> dict[str, Any]:
            instance = target_class(**constructor_kwargs)
            method = getattr(instance, method_name)
            outcome = method(ctx)
            return cls.outcome_to_dict(outcome)
        return handler

    @classmethod
    def outcome_to_dict(
        cls, outcome: "Outcome[Any]",
    ) -> dict[str, Any]:
        """Convert an ``Outcome`` to the dict shape that
        ``infrastructure.promises.dispatcher._ensure_job`` reads.

        Success → ``{"status": "ok", "evidence": ...}``. Failure →
        ``{"error": message, "transient": bool, "evidence": ...}``.
        ``transient`` lets the orchestrator's cooldown machinery
        distinguish a network blip (retry next tick) from a 4xx
        config-error (back off until operator action).
        """
        if outcome.ok:
            return {
                "status": "ok",
                "evidence": dict(outcome.evidence or {}),
            }
        return {
            "error": outcome.error or "ensurer failure",
            "transient": bool(outcome.transient),
            "evidence": dict(outcome.evidence or {}),
        }


__all__ = ["LifecycleHandlerAdapter"]
