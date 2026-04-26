"""Shim — moved to
``media_stack.application.media_server_adapters.planned`` in ADR-0002
Phase 16-E (media_server_adapters). Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so existing test patches
of the form ``mock.patch.object(MODULE, "_helper", ...)`` (where
``MODULE`` is the legacy shim path) work transparently — the shim
import resolves to the impl module itself, so attribute patches land
on the same module the impl function's body looks up names from.

The legacy import path ``media_stack.services.media_server_adapters.planned:PlannedMediaServerAdapter``
is the supertype the per-app adapters at
``services/apps/<tech>/media_server_adapter.py`` inherit from — keep
this shim until those imports are rebased.
"""

import sys

from media_stack.application.media_server_adapters import planned as _impl

sys.modules[__name__] = _impl
