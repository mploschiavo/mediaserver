"""Shim — moved to ``media_stack.application.qbittorrent.runtime_ops``
in ADR-0002 Phase 16-D batch 3 (download clients — qbittorrent).
Phase 16-F removes this shim.

The new module re-exports every callable that the legacy module
exposed so existing callers and the
contracts/services/qbittorrent.yaml entry-point handlers keep
working.
"""

import sys

from media_stack.application.qbittorrent import runtime_ops as _impl

sys.modules[__name__] = _impl
