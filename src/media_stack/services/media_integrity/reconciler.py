"""Shim — moved to ``media_stack.application.media_integrity.reconciler``
in ADR-0002 Phase 16-E (cross-cutting media-integrity). Phase 16-F
removes this shim.

Aliases ``sys.modules`` to the impl module so existing test patches
of the form ``mock.patch.object(MODULE, "_helper", ...)`` (where
``MODULE`` is the legacy shim path) work transparently — the shim
import resolves to the impl module itself, so attribute patches land
on the same module the impl function's body looks up names from.
"""

import sys

from media_stack.application.media_integrity import reconciler as _impl

sys.modules[__name__] = _impl
