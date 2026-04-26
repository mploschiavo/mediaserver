"""qBittorrent app package — Phase 16-D batch 3 shim.

ADR-0002 Phase 16-D batch 3 (download clients — qbittorrent) moved
this package's contents into the hexagonal layout under
``application/qbittorrent/``, ``adapters/qbittorrent/``, and
``infrastructure/qbittorrent/``. The old
``services.apps.qbittorrent.*`` import paths remain as re-export
shims so existing callers and contracts/services/qbittorrent.yaml
entry-points keep working. Phase 16-F removes these shims.
"""

from media_stack.application.qbittorrent.service import QBittorrentService

__all__ = ["QBittorrentService"]
