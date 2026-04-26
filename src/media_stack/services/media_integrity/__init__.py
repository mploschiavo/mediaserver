"""Shim — moved to ``media_stack.application.media_integrity``,
``media_stack.domain.media_integrity``,
``media_stack.adapters.media_integrity``, and
``media_stack.infrastructure.media_integrity`` in ADR-0002 Phase
16-E (cross-cutting media-integrity). Phase 16-F removes this shim.

The legacy ``services.media_integrity`` package re-exports the
public surface from the new locations and side-effect-imports the
relocated leaf modules so existing call sites
(``contracts/services/media_integrity.yaml`` job-handler entries,
the ``api.services.media_integrity_handlers`` controller, and the
direct test imports under ``tests/unit/media_integrity``) keep
working unchanged.

This module cannot use the ``sys.modules[__name__] = _impl`` trick
the leaf shims use because it is a package — replacing the package
object would break the
``services.media_integrity.{adapters,policy,...}`` import paths.
Instead we re-export the public names explicitly and let the per-
module shims handle the per-module aliasing.
"""

from __future__ import annotations

# Domain re-exports
from media_stack.domain.media_integrity.arr_protocol import (
    AdapterCapabilities,
    ArrApp,
    MediaFile,
    MediaRelease,
    QualityProfile,
)
from media_stack.domain.media_integrity.bazarr_protocol import (
    BazarrApp,
    BazarrCapabilities,
    SubtitleFile,
    SubtitleRelease,
)
from media_stack.domain.media_integrity.policy import (
    BazarrSection,
    MediaManagementSection,
    NamingSection,
    QualitySection,
    ServarrPolicy,
)

# Application re-exports
from media_stack.application.media_integrity.service import (
    MediaIntegrityInProgress,
    MediaIntegrityService,
)

# Side-effect imports — pull the leaf shims into ``sys.modules`` so
# ``import media_stack.services.media_integrity.policy`` (etc.) lands
# on the new impls via the alias.
from . import (  # noqa: F401,E402  side-effect: alias leaf modules
    _secret_scrub,
    arr_protocol,
    bazarr_protocol,
    enforcer,
    factory,
    job_handlers,
    policy,
    reconciler,
    service,
    subtitle_reconciler,
)
from . import adapters  # noqa: F401,E402  shim sub-package

__all__ = [
    "AdapterCapabilities",
    "ArrApp",
    "BazarrApp",
    "BazarrCapabilities",
    "BazarrSection",
    "MediaFile",
    "MediaIntegrityInProgress",
    "MediaIntegrityService",
    "MediaManagementSection",
    "MediaRelease",
    "NamingSection",
    "QualityProfile",
    "QualitySection",
    "ServarrPolicy",
    "SubtitleFile",
    "SubtitleRelease",
]
