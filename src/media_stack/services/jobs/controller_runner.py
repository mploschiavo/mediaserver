"""Shim — moved to ``media_stack.application.jobs.controller_runner``
in ADR-0002 Phase 16-E. Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so the legacy
``services.jobs.controller_runner`` import path and the new
``application.jobs.controller_runner`` path resolve to the same
module object. The config-policy resolver + reusable runner builder
used by the action handlers now lives in the application layer.
"""

import sys

from media_stack.application.jobs import controller_runner as _impl

sys.modules[__name__] = _impl
