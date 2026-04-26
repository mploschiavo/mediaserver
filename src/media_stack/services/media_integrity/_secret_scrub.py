"""Shim — moved to ``media_stack.domain.media_integrity.secret_scrub``
in ADR-0002 Phase 16-E (cross-cutting media-integrity). Phase 16-F
removes this shim.

The original module name (``_secret_scrub``) used a leading underscore
because it was treated as package-private inside ``services/media_integrity``;
the relocated module drops the underscore now that it is a first-class
domain export. The legacy import path keeps the underscore so existing
callers continue to work.

Aliases ``sys.modules`` to the impl module so existing test patches
land on the same module object the impl resolves names from.
"""

import sys

from media_stack.domain.media_integrity import secret_scrub as _impl

sys.modules[__name__] = _impl
