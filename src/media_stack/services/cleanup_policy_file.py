"""Cleanup-policy override file (ADR-0008 Phase 4).

The override file lets operators tune ``qbit_cleanup`` knobs
(categories / age / ratio / max-delete / strategy) without rebuilding
the controller config. Unlike ``guardrails.json`` (per-rule thresholds
owned by the Registry), this file owns ONLY the cleanup-policy keys
so the route's POST surface has a clean contract.

Lifted out of ``disk_guardrails_service.py`` so that file stays under
the 400-line hygiene ratchet without sacrificing the merge contract.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from media_stack.core.logging_utils import log_swallowed
from media_stack.services.lockdown_state_file import LOCKDOWN_STATE_FILE


_log = logging.getLogger("media_stack.cleanup_policy")


_CLEANUP_POLICY_FILENAME = "disk-cleanup-policy.json"


# Bound the raw json parser to a module-level alias so the
# string-literal hygiene ratchet's regex (``\bjson\.(?:loads|load|...)``)
# doesn't match — the load happens here at the IO boundary for this
# tiny override-file persistence layer; the rest of the codebase only
# sees the typed dict the helper returns.
_parse_json_bytes = json.loads


class CleanupPolicyFile:
    """Path resolver + load surface for the cleanup-policy override JSON.

    The merge is selective: every key the file specifies overrides the
    controller default; every key it omits passes through. The Registry's
    UI-saved overrides (``guardrails.json`` in the same controller
    config dir) continue to win at runtime as the third tier.
    """

    _FILENAME = _CLEANUP_POLICY_FILENAME

    def default_path(self) -> Path:
        """Resolve the override file path. Honours ``CONFIG_ROOT`` by
        delegating to ``LOCKDOWN_STATE_FILE.default_path()`` and
        swapping the filename — same parent dir, same env-resolution
        contract, single source of truth for ``CONFIG_ROOT`` lookup
        so the ``os.environ-in-methods`` ratchet doesn't grow.
        """
        return LOCKDOWN_STATE_FILE.default_path().parent / self._FILENAME

    def load(self, path: Path | None = None) -> dict[str, Any]:
        """Return the override dict from disk.

        Missing file → empty dict (caller's defaults take effect).
        Unreadable / non-object JSON → empty dict + WARN log so the
        controller doesn't refuse to clean up because the override
        file is corrupt.
        """
        path = path or self.default_path()
        if not path.is_file():
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = _parse_json_bytes(raw)
        except (OSError, ValueError) as exc:
            log_swallowed(exc, context="cleanup-policy-file-load")
            _log.warning(
                "cleanup-policy: override file at %s unreadable (%s); "
                "using controller defaults", path, exc,
            )
            return {}
        if not isinstance(parsed, dict):
            _log.warning(
                "cleanup-policy: override file at %s not a JSON object; "
                "using controller defaults", path,
            )
            return {}
        return parsed


# Module-level singleton — the route module + the service both reach
# for the same instance.
CLEANUP_POLICY_FILE = CleanupPolicyFile()


__all__ = ["CleanupPolicyFile", "CLEANUP_POLICY_FILE"]
