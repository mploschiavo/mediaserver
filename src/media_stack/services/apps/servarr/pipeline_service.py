"""Shim — moved to ``media_stack.application.servarr.pipeline_service`` in
ADR-0002 Phase 16-D. Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so existing test patches
(``mock.patch.object(MODULE, "_helper", ...)``) work transparently.
"""

import sys

from media_stack.application.servarr import pipeline_service as _impl

sys.modules[__name__] = _impl
