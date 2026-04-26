"""Shim — moved to
``media_stack.adapters.qbittorrent.download_client_adapter`` in
ADR-0002 Phase 16-D batch 3 (download clients — qbittorrent).
Phase 16-F removes this shim.
"""

import sys

from media_stack.adapters.qbittorrent import download_client_adapter as _impl

sys.modules[__name__] = _impl
