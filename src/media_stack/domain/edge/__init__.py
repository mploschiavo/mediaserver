"""Edge domain — ADR-0002 Phase 16-E placeholder.

The edge subsystem has no domain types of its own yet; it composes
shared port types defined under ``core/edge`` and tech-specific
generators under ``infrastructure/edge``. This package exists so
the hexagonal layout is consistent across cross-cutting subsystems
and so future edge-only invariants land in a stable home.
"""
