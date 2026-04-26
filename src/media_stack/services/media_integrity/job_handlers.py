"""Shim — moved to
``media_stack.application.media_integrity.job_handlers`` in
ADR-0002 Phase 16-E (cross-cutting media-integrity). Phase 16-F
removes this shim.

The four job handlers referenced by
``contracts/services/media_integrity.yaml`` resolve through this
shim's ``sys.modules`` alias: a contract entry like
``handler: "media_stack.services.media_integrity.job_handlers:media_integrity_scan"``
imports this module, which is aliased to the impl, so attribute
lookup ``:media_integrity_scan`` lands on the application-layer
function. Tests that patch ``set_review_params`` against the legacy
path also work transparently.
"""

import sys

from media_stack.application.media_integrity import job_handlers as _impl

sys.modules[__name__] = _impl
