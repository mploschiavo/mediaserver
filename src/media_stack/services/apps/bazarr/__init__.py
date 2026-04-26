"""Bazarr app package — Phase 16-D batch 4 shim.

ADR-0002 Phase 16-D batch 4 (bazarr) moved this package's contents
into the hexagonal layout under ``domain/bazarr/``,
``application/bazarr/``, ``adapters/bazarr/``, and
``infrastructure/bazarr/``. The old ``services.apps.bazarr.*`` import
paths remain as re-export shims so existing callers and
contracts/services/bazarr.yaml entry-points keep working.
Phase 16-F removes these shims.
"""

from media_stack.application.bazarr.service import BazarrService

__all__ = ["BazarrService"]
