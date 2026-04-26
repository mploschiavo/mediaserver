"""Shim — moved to
``media_stack.application.qbittorrent.configure_categories_job`` in
ADR-0002 Phase 16-D batch 3 (download clients — qbittorrent).
Phase 16-F removes this shim.
"""

import sys

from media_stack.application.qbittorrent import configure_categories_job as _impl

sys.modules[__name__] = _impl
