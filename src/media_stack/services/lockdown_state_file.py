"""Lockdown state file (ADR-0008 Phase 1).

Path resolver + canonical empty-state shape for the lockdown state
file. Lifted out of ``download_lockdown_service.py`` so that file
stays under the 400-line hygiene ratchet.

The state-collector and the ``DownloadLockdownService`` both reach
for the same module-level singleton (``LOCKDOWN_STATE_FILE``) — single
source of truth for where the state file lives.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from media_stack.core.logging_utils import log_swallowed


_log = logging.getLogger("media_stack.lockdown")


_STATE_FILE_DEFAULT = "/srv-config/.controller/disk-lockdown.state.json"

_TRIGGER_AUTO = "auto"
_TRIGGER_MANUAL = "manual"


class LockdownStateFile:
    """Path resolver + canonical empty-state shape for the lockdown
    state file.

    Lives as a class so the no-loose-functions ratchet stays clean.
    """

    _FILE_DEFAULT = _STATE_FILE_DEFAULT

    def default_path(self) -> Path:
        """Resolve the lockdown-state JSON path. Honours
        ``CONFIG_ROOT``, matching ``GuardrailRegistry``'s
        ``_override_path`` semantics so operators only have one root
        knob to set."""
        config_root = os.environ.get("CONFIG_ROOT", "")
        if config_root:
            return Path(config_root) / ".controller" / "disk-lockdown.state.json"
        return Path(self._FILE_DEFAULT)

    def empty_state(self) -> dict[str, Any]:
        """Canonical "not-engaged" state shape. Centralised so both
        the service's first-load, ``release()``'s reset path, and the
        state collector's snapshot all produce identical JSON."""
        return {
            "engaged": False,
            "trigger": None,
            "engaged_at": 0.0,
            "engaged_by": "",
            "auto_check_paused_until": None,
            "paused_clients": [],
            "last_failures": [],
        }

    def load_state(self, path: Path) -> dict[str, Any]:
        """Read + sanitise the state file at ``path``. Missing /
        unreadable / corrupt JSON returns the canonical empty shape
        so the service never KeyErrors on first boot."""
        if not path.is_file():
            return self.empty_state()
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning(
                "lockdown: state file at %s unreadable (%s); "
                "starting fresh", path, exc,
            )
            return self.empty_state()
        if not isinstance(parsed, dict):
            _log.warning(
                "lockdown: state file at %s not a JSON object; "
                "starting fresh", path,
            )
            return self.empty_state()
        out = self.empty_state()
        for key in out:
            if key in parsed:
                out[key] = parsed[key]
        out["paused_clients"] = [
            str(c) for c in (out.get("paused_clients") or [])
            if isinstance(c, (str, int))
        ]
        if out.get("trigger") not in (None, _TRIGGER_AUTO, _TRIGGER_MANUAL):
            out["trigger"] = None
        return out

    def save_state(self, path: Path, state: Mapping[str, Any]) -> None:
        """Persist ``state`` to ``path`` atomically via tempfile +
        os.replace. Failures log + swallow so the lockdown action
        proceeds; the next save retries the same path."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log_swallowed(exc, context="lockdown_state_mkdir")
            _log.warning(
                "lockdown: could not create state directory %s: %s",
                path.parent, exc,
            )
            return
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8",
                dir=str(path.parent),
                prefix=path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                payload = json.dumps(dict(state), indent=2, sort_keys=True)
                tmp.write(payload + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name
            os.replace(tmp_path, path)
        except OSError as exc:
            log_swallowed(exc, context="lockdown_state_save")
            _log.warning(
                "lockdown: failed to persist state at %s: %s", path, exc,
            )


# Module-level singleton — the state-collector and the service both
# reach for the same instance.
LOCKDOWN_STATE_FILE = LockdownStateFile()


__all__ = ["LockdownStateFile", "LOCKDOWN_STATE_FILE"]
