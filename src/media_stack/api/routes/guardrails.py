"""Guardrails-domain GET routes (ADR-0007 Phase 2).

Covers the cross-domain guardrail registry snapshot at
``GET /api/guardrails``. The legacy elif chain in
``handlers_get.GetRequestHandler.handle()`` had a single GET path
under this domain (the ``{id}`` POST sub-routes are mutators and
live in ``handlers_post``); Phase 2 migrates that one route off
the chain onto the Router.

The body is lifted verbatim from the legacy chain so downstream
consumers (the dashboard's Guardrails page) see no shape change.
The handler:

  * Pulls the default ``GuardrailRegistry`` (side-effect-registers
    every domain rule on first import) and emits its
    ``status_summary()``.
  * Resolves the evaluation cadence via
    ``application.guardrails.evaluation_loop._resolved_interval``.
    The cadence is operator-tunable through the
    ``MEDIA_STACK_GUARDRAIL_INTERVAL_SECONDS`` env var or the
    ``POST /api/guardrails/config`` endpoint; surfacing it in the
    GET payload lets the UI render an editable input plus the
    "next evaluation in" header hint without a second round-trip.

Defensive fallback: if the evaluation-loop import or
``_resolved_interval`` call fails (it dereferences a profile
object that may not be wired in early-bootstrap or test contexts),
the handler falls back to the 300-second default rather than
blowing up the dashboard. This mirrors the legacy chain's
behaviour exactly.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get

# Cadence (seconds) used when the evaluation-loop module declines
# to resolve an interval (e.g. early-bootstrap, profile not yet
# wired). Matches the default exposed by ``_resolved_interval``
# itself; lifted verbatim from the legacy elif chain.
_DEFAULT_GUARDRAIL_INTERVAL_SECONDS = 300


class GuardrailsGetRoutes(RouteModule):
    """Cross-domain guardrails registry GET routes. The Router
    auto-discovers and instantiates this class at startup, then
    walks tagged methods for registration."""

    @get("/api/guardrails")
    def handle_guardrails_registry(self, handler: Any) -> None:
        """Return the guardrail-registry snapshot plus the
        evaluation cadence.

        Response shape::

            {
              "guardrails": [<status entry>, ...],
              "evaluation_interval_seconds": <int>
            }

        The ``guardrails`` array is whatever ``status_summary()``
        emits — every registered rule with its current threshold,
        last evaluation status, and last-fire timestamp across the
        storage / bandwidth / external_api / media_quality /
        job_health / auth / dependency / cost domains.

        The ``evaluation_interval_seconds`` integer reflects the
        cadence the evaluation loop is currently using. On any
        failure to resolve it (import error, profile missing) we
        fall back to ``_DEFAULT_GUARDRAIL_INTERVAL_SECONDS`` so the
        UI always has a number to render.
        """
        from media_stack.services import guardrails as guardrails_pkg

        registry = guardrails_pkg.default()
        try:
            from media_stack.application.guardrails.evaluation_loop import (
                _resolved_interval,
            )
            interval = int(_resolved_interval(None))
        except Exception:  # noqa: BLE001
            # Any failure resolving the cadence falls back to the
            # default — this matches the legacy chain's behaviour
            # and keeps the dashboard rendering even when the
            # profile isn't wired yet (early-bootstrap window).
            interval = _DEFAULT_GUARDRAIL_INTERVAL_SECONDS
        handler._json_response(HTTPStatus.OK, {
            "guardrails": registry.status_summary(),
            "evaluation_interval_seconds": interval,
        })


__all__ = ["GuardrailsGetRoutes"]
