"""Shim — moved to
``media_stack.adapters.media_integrity._servarr_base`` in ADR-0002
Phase 16-E (cross-cutting media-integrity). Phase 16-F removes this
shim.

Aliases ``sys.modules`` to the impl module so existing test patches
of the form ``mock.patch.object(MODULE, "_helper", ...)`` (where
``MODULE`` is the legacy shim path) work transparently — the shim
import resolves to the impl module itself, so attribute patches land
on the same module the impl function's body looks up names from.

The structural ``isinstance(exc, ServarrHttpError)`` check inside
``domain.media_integrity.secret_scrub`` imports
``ServarrHttpError`` from the new path, so an exception raised from
the legacy shim (which IS the same class object — sys.modules alias)
is recognised correctly.
"""

import sys

from media_stack.adapters.media_integrity import _servarr_base as _impl

sys.modules[__name__] = _impl
