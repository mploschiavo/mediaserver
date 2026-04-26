"""Shim — moved to ``media_stack.domain.media_integrity.policy`` in
ADR-0002 Phase 16-E (cross-cutting media-integrity). Phase 16-F
removes this shim.

Aliases ``sys.modules`` to the impl module so existing test patches
of the form ``monkeypatch.setattr("media_stack.services.media_integrity.policy._default_contract_path", ...)``
land on the same module object the impl resolves names from. The
``_CONTRACT_PATH_*`` attributes the
``test_policy_path_candidates_ratchet`` reads also resolve through
this alias.
"""

import sys

from media_stack.domain.media_integrity import policy as _impl

sys.modules[__name__] = _impl
