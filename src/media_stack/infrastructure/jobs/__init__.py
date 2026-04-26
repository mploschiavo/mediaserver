"""Jobs infrastructure layer — composition-root + I/O-touching helpers.

ADR-0002 Phase 16-E (cross-cutting jobs) — the I/O-touching half of
the jobs subsystem lives here. Pure types live in
``media_stack.domain.jobs``; orchestration / runtime dispatch lives in
``media_stack.application.jobs``.

Modules:
- ``bootstrap_config_generator.py`` — generates the bootstrap config
  JSON from the per-service contracts + profile YAML. Touches the
  filesystem (reads YAML, writes JSON), so it lives at the
  infrastructure layer rather than application.

The legacy ``services.jobs.bootstrap_config_generator`` import path
remains as a re-export shim through Phase 16-F.
"""
