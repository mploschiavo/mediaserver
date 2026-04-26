"""Shim — moved to ``media_stack.application.jobs.action_handlers`` in
ADR-0002 Phase 16-E. Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so the legacy
``services.jobs.action_handlers`` import path and the new
``application.jobs.action_handlers`` path resolve to the same module
object. ``action_*`` callables remain dispatched via ``POST
/api/actions/<name>`` from ``ActionHandlerService`` — the dispatch
contract is preserved unchanged because the shim transparently
redirects to the impl module.
"""

import sys

from media_stack.application.jobs import action_handlers as _impl

sys.modules[__name__] = _impl
