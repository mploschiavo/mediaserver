"""Shim — moved to
``media_stack.adapters.media_server_adapters.emby`` in ADR-0002
Phase 16-E (media_server_adapters). Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so existing test patches
of the form ``mock.patch.object(MODULE, "_helper", ...)`` (where
``MODULE`` is the legacy shim path) work transparently — the shim
import resolves to the impl module itself, so attribute patches land
on the same module the impl function's body looks up names from.

The legacy import path ``media_stack.services.media_server_adapters.emby:EmbyMediaServerAdapter``
is referenced by ``contracts/services/emby.yaml`` — keep this shim
until that manifest entry is relocated.
"""

import sys

from media_stack.adapters.media_server_adapters import emby as _impl

sys.modules[__name__] = _impl
