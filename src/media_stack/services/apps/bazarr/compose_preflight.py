"""Shim — moved to
``media_stack.infrastructure.bazarr.compose_preflight``.

Aliases ``sys.modules`` to the impl module so existing test patches
of the form ``mock.patch.object(MODULE, "_helper", ...)`` (where
``MODULE`` is the legacy shim path) work transparently — the shim
import resolves to the impl module itself, so attribute patches land
on the same module the impl function's body looks up names from.
Mirrors the sabnzbd / qbittorrent / jellyfin shim shape under
``services/apps/<svc>/compose_preflight.py``.
"""

import sys

from media_stack.infrastructure.bazarr import compose_preflight as _impl

sys.modules[__name__] = _impl
