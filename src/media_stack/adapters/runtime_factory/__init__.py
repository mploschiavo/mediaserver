"""Runtime factory adapters layer.

ADR-0002 Phase 16-E (cross-cutting runtime_factory) — placeholder
package. The runtime factory currently expresses every external
dependency through ``ControllerRuntimeFactoryDependencies`` callbacks
(API-key reads, deep-merge, env truthy, etc.) wired by the CLI
composition root in ``services/jobs/controller_runner.py``, so
there are no port implementations to host here yet. Kept as an
empty package so the four-layer naming pattern matches the other
cross-cutting subsystems migrated in Phase 16-E.
"""
