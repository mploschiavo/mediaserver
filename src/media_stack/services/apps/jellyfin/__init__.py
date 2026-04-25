"""Jellyfin app package — Phase 16-D batch 1 shim.

ADR-0002 Phase 16-D batch 1 (jellyfin) moved this package's contents
into the hexagonal layout under ``domain/jellyfin/``,
``application/jellyfin/``, ``adapters/jellyfin/``, and
``infrastructure/jellyfin/``. The old ``services.apps.jellyfin.*``
import paths remain as re-export shims so existing callers and
contracts/services/jellyfin.yaml entry-points keep working.
Phase 16-F removes these shims.
"""

__all__: list[str] = []
