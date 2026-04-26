"""Jobs domain types — pure value objects + the ``Job`` data class.

ADR-0002 Phase 16-E (cross-cutting jobs) — only the I/O-free,
framework-free parts of the job framework live here:

- ``types.py`` — the ``Job`` data class (a unit of work with prereqs +
  sub-jobs + ``after:`` ordering), the ``CancelledError`` exception
  type, the ``_noop`` placeholder handler used by composite jobs, and
  the ``_normalize_source`` / ``_HISTORY_SOURCE_VALUES`` history-tag
  schema.

The job runner, dispatcher, prerequisite registry, action handlers,
and controller wiring all live in ``media_stack.application.jobs``
because they perform I/O (filesystem reads, subprocess spawns, HTTP
calls) at run time.

Bootstrap config generation lives in
``media_stack.infrastructure.jobs.bootstrap_config_generator`` because
it is a composition-root concern that touches contracts on disk.

This package may be imported from ``application/``, ``adapters/``,
and ``infrastructure/`` freely — it depends on nothing outside the
standard library.
"""

from media_stack.domain.jobs.types import (
    CancelledError,
    Job,
    PREREQS,
    _HISTORY_SOURCE_VALUES,
    _JOB_HISTORY_MAX,
    _noop,
    _normalize_source,
    register_prereq,
)


__all__ = [
    "CancelledError",
    "Job",
    "PREREQS",
    "_HISTORY_SOURCE_VALUES",
    "_JOB_HISTORY_MAX",
    "_noop",
    "_normalize_source",
    "register_prereq",
]
