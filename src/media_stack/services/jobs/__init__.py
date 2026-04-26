"""Jobs package — Phase 16-E shim.

ADR-0002 Phase 16-E (cross-cutting jobs) split this package's
contents across the hexagonal layers:

- Pure value objects (``Job``, ``CancelledError``, ``Job.noop``,
  ``PREREQS``, the history-schema constants) live in
  ``media_stack.domain.jobs.types``.
- Orchestration / runtime dispatch (``JobRunner``, ``JobContext``,
  ``ActionHandlerService``, controller handler-spec loader / runner
  builder) lives under ``media_stack.application.jobs``.
- Bootstrap config generation (filesystem I/O) lives in
  ``media_stack.infrastructure.jobs.bootstrap_config_generator``.

Each legacy submodule (``framework``, ``action_handlers``,
``controller_handlers``, ``controller_runner``,
``bootstrap_config_generator``) keeps a ``sys.modules[__name__] =
_impl`` shim so existing callers — including the
``media-stack-generate-bootstrap-config`` console-script entry-point
in ``pyproject.toml`` — and contracts/services/*.yaml
plugin.jobs.<name>.handler refs keep resolving. Phase 16-F removes
these shims.
"""
