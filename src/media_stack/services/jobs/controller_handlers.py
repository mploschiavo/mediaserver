"""Shim — moved to ``media_stack.application.jobs.controller_handlers``
in ADR-0002 Phase 16-E. Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so the legacy
``services.jobs.controller_handlers`` import path and the new
``application.jobs.controller_handlers`` path resolve to the same
module object. The handler-spec loader + executor for the bootstrap
controller's preflight and post-bootstrap phases now lives in the
application layer.
"""

import sys

from media_stack.application.jobs import controller_handlers as _impl

sys.modules[__name__] = _impl
