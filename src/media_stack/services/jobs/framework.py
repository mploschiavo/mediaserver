"""Shim — moved to ``media_stack.application.jobs.framework`` in
ADR-0002 Phase 16-E. Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so existing test patches
of the form ``mock.patch.object(MODULE, "_record_history", ...)``
(where ``MODULE`` is the legacy shim path) work transparently — the
shim import resolves to the impl module itself, so attribute patches
land on the same module the impl function's body looks up names from.

The pure value objects (``Job``, ``CancelledError``, ``Job.noop``,
``PREREQS``, the history-schema constants) live one layer further
down in ``media_stack.domain.jobs.types`` — the application module
re-exports them at module scope so ``from
media_stack.services.jobs.framework import Job`` still resolves.
"""

import sys

from media_stack.application.jobs import framework as _impl

sys.modules[__name__] = _impl
