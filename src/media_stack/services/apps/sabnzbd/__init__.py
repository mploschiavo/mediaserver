"""SABnzbd app package — Phase 16-D batch 3 shim.

ADR-0002 Phase 16-D batch 3 (download clients) moved this package's
contents into the hexagonal layout under ``application/sabnzbd/``,
``adapters/sabnzbd/``, and ``infrastructure/sabnzbd/``. The old
``services.apps.sabnzbd.*`` import paths remain as re-export shims so
existing callers and contracts/services/sabnzbd.yaml entry-points keep
working. Phase 16-F removes these shims.
"""

from media_stack.application.sabnzbd.service import SabnzbdService

__all__ = ["SabnzbdService"]
