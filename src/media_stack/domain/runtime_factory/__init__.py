"""Pure runtime-factory domain types.

ADR-0002 Phase 16-E (cross-cutting runtime_factory) — value objects,
type aliases, and the plan-summary transform that turns a fully
populated ``ControllerRuntime`` into the log-line + feature-flag
snapshot the bootstrap orchestrator emits.

No HTTP, no filesystem I/O, no environment lookups. Anything that
touches the outside world lives in
``media_stack.infrastructure.runtime_factory`` (config loading) or
``media_stack.application.runtime_factory`` (orchestration).
"""
