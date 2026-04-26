"""Guardrails infrastructure — intentionally empty.

ADR-0002 Phase 16-E (cross-cutting guardrails) — the guardrails
subsystem reads collected state via short-lived dynamic imports of
existing services (``api.services.disk``, ``services.jobs.framework``)
inside ``application/guardrails/state_collector.py``. Those imports
are the boundary; the boundary itself does not own enough plumbing
to deserve its own infrastructure module today.
"""
