"""Jobs application layer — runner, dispatcher, action handlers.

ADR-0002 Phase 16-E (cross-cutting jobs) — the orchestration half of
the jobs subsystem. Pure types live in ``media_stack.domain.jobs``;
filesystem-touching helpers (bootstrap config generation) live in
``media_stack.infrastructure.jobs``. Everything that walks a job
tree, satisfies prerequisites, persists run history, or dispatches a
named action lives here.

Modules:
- ``framework.py``           — ``JobRunner`` + ``JobContext`` + the
                               YAML-driven job-tree builder. The
                               named prereq registry lives in
                               ``domain.jobs`` so ``Job.check_prereqs``
                               doesn't invert the hexagon; this module
                               registers concrete prereq callables on
                               that registry at import time.
- ``action_handlers.py``     — ``ActionHandlerService`` and the
                               module-level ``action_*`` callables
                               that ``POST /api/actions/<name>``
                               dispatches into.
- ``controller_handlers.py`` — handler-spec loader + executor for
                               the bootstrap controller's preflight
                               and post-bootstrap phases.
- ``controller_runner.py``   — config-policy resolver + reusable
                               runner builder used by the action
                               handlers.

Importing this package does NOT side-effect-register anything on the
default registry — a side-effect import in ``framework.py`` registers
the named prereqs there, but no other module-level state exists. The
service is composed by ``cli.commands.controller_main`` and exposed
through the legacy ``services.jobs`` shim.
"""
