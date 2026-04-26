"""Shim — moved to ``media_stack.application.qbittorrent.service`` in
ADR-0002 Phase 16-D batch 3 (download clients — qbittorrent).
Phase 16-F removes this shim.
"""

import sys

from media_stack.application.qbittorrent import service as _impl

sys.modules[__name__] = _impl
