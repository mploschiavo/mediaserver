"""Edge application — ADR-0002 Phase 16-E placeholder.

No application-layer orchestration lives here yet; the edge
subsystem's main use-case is the Envoy config generator which
runs as a one-shot init container and lives under
``infrastructure/edge``. This package exists so the hexagonal
layout is consistent across cross-cutting subsystems.
"""
