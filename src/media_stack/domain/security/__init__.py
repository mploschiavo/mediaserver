"""Security domain — pure value objects and transforms.

ADR-0002 Phase 16-E (cross-cutting security) — reserved for the
pure-data half of the security subsystem. Today the security DTOs
(``SessionDTO``, ``APITokenRecord``, the report alert dataclasses)
ship alongside their orchestrators in
``media_stack.application.security`` because they are public-API
return shapes tightly coupled to the use cases that produce them;
extracting them down into ``domain/security/`` would be ceremony
without a caller.

Phase 16-F or a subsequent migration may relocate the standalone
shapes here once a consumer needs them outside the orchestrator
context. Until then this package is intentionally empty — its
presence locks the layout in place so the layering ratchet can
detect future cross-layer drift.
"""
