"""DownloadLockdownService — engage / release the download-client
lockdown tier on top of the existing GuardrailRegistry (ADR-0008
Phase 1).

The service owns three concerns:

  1. Per-client failure isolation. A failed pause logs ``[WARN]`` and
     is recorded in ``failures`` but does not block the rest of the
     clients. The service treats the ``paused_clients`` list as
     authoritative for what to resume on release — only resume what
     was actually paused successfully.
  2. State persistence at ``CONFIG_ROOT/.controller/disk-lockdown.state.json``
     (resolved exactly like ``GuardrailRegistry``'s overrides file).
     Atomic save via ``tempfile + os.replace``. Load is permissive
     — corrupt JSON / missing keys → start fresh, log warn.
  3. Idempotence. ``engage`` while already engaged is a no-op except
     for refreshing the audit timestamp; ``release`` while not
     engaged is a no-op.

The service does NOT:

  * Decide WHEN to engage. That's the ``_LockdownThreshold`` rule's
    job (in ``application/guardrails/domains/storage.py``).
  * Tick on its own. The ``evaluation_loop.tick()`` dispatcher is
    the orchestration layer that wires rule → service.
  * Talk to download clients directly. Each per-client adapter in
    ``adapters/_shared/download_client_lockdown.py`` owns its own
    HTTP shape; the service just iterates them.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from media_stack.adapters._shared.download_client_lockdown import (
    DownloadClientLockdown,
)
from media_stack.core.logging_utils import log_swallowed


_log = logging.getLogger("media_stack.lockdown")


_STATE_FILE_DEFAULT = "/srv-config/.controller/disk-lockdown.state.json"

_TRIGGER_AUTO = "auto"
_TRIGGER_MANUAL = "manual"
_VALID_TRIGGERS = (_TRIGGER_AUTO, _TRIGGER_MANUAL)


class LockdownStateFile:
    """Path resolver + canonical empty-state shape for the lockdown
    state file.

    Lives as a class so the no-loose-functions ratchet stays clean
    and so the state-collector can call into the same resolver the
    ``DownloadLockdownService`` uses (single source of truth for
    where the state file lives).
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


# Module-level singleton — the state-collector and the service both
# reach for the same instance.
LOCKDOWN_STATE_FILE = LockdownStateFile()


class DownloadLockdownService:
    """Engage / release download-client lockdown.

    Constructor-injected collaborators:

      * ``adapters`` — iterable of ``DownloadClientLockdown``-shaped
        objects. The service iterates them in order; failures don't
        block the rest. For tests, pass a list of fakes; in
        production, the wirer hands a Sonarr / Radarr / qBit / SAB
        bundle.
      * ``state_path_fn`` — function returning the on-disk state
        path. Defaults to ``LOCKDOWN_STATE_FILE.default_path`` (which
        honours ``CONFIG_ROOT``); tests inject a tmp-path lambda.
      * ``clock`` — ``time.time``-like callable for deterministic
        timestamps in tests.
    """

    def __init__(
        self,
        adapters: Iterable[DownloadClientLockdown],
        *,
        state_path_fn: "callable[[], Path] | None" = None,
        clock: "callable[[], float] | None" = None,
    ) -> None:
        self._adapters: list[DownloadClientLockdown] = list(adapters)
        self._state_path_fn = state_path_fn or LOCKDOWN_STATE_FILE.default_path
        self._clock = clock or time.time

    # -- public API --------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Return the current state dict. Always reads from disk so
        a controller restart sees the post-restart truth, not an
        in-memory cache that the previous process built up."""
        return self._load_state()

    def engage(
        self, *, trigger: str, by: str,
    ) -> dict[str, Any]:
        """Pause every download client. Idempotent.

        ``trigger`` must be ``"auto"`` or ``"manual"``. ``by`` is a
        free-form actor string for the audit trail (``"auto:disk-78%"``
        or ``"operator:matthew"``).

        Returns ``{"paused_clients": [...], "failures": [...],
        "engaged": True, "trigger": ..., "already_engaged": bool}``.
        """
        if trigger not in _VALID_TRIGGERS:
            raise ValueError(
                f"trigger must be one of {_VALID_TRIGGERS}, got {trigger!r}",
            )
        state = self._load_state()
        now = float(self._clock())
        if state.get("engaged"):
            # Idempotent: refresh timestamp / actor only. Don't
            # re-pause already-paused clients (engage is meant to be
            # safe to re-call but we shouldn't be hammering the API).
            state["engaged_at"] = now
            state["engaged_by"] = by
            # Honor a manual upgrade of an auto-engaged lockdown:
            # operator-side stickiness wins over auto-release.
            if trigger == _TRIGGER_MANUAL:
                state["trigger"] = _TRIGGER_MANUAL
            self._save_state(state)
            return {
                "paused_clients": list(state.get("paused_clients") or []),
                "failures": [],
                "engaged": True,
                "trigger": state.get("trigger"),
                "already_engaged": True,
            }

        paused: list[str] = []
        failures: list[dict[str, Any]] = []
        for adapter in self._adapters:
            ok = self._per_client_pause(adapter)
            if ok:
                paused.append(adapter.client_id)
            else:
                failures.append({
                    "client": adapter.client_id,
                    "action": "pause",
                })

        new_state = {
            "engaged": True,
            "trigger": trigger,
            "engaged_at": now,
            "engaged_by": by,
            "auto_check_paused_until": None,
            "paused_clients": paused,
            "last_failures": failures,
        }
        self._save_state(new_state)
        return {
            "paused_clients": paused,
            "failures": failures,
            "engaged": True,
            "trigger": trigger,
            "already_engaged": False,
        }

    def pause_auto(self, *, hours: int, by: str) -> dict[str, Any]:
        """Set the auto-check pause TTL on the state file.

        While ``auto_check_paused_until`` is in the future, the
        ``_LockdownThreshold`` rule's ``evaluate()`` short-circuits to
        ``None`` so AUTO-side engages stop firing. Already-paused
        clients stay paused — release is an explicit operator action.

        Idempotent / monotonic: a later call extends the TTL but never
        decrements it. Callers can clear the bypass by passing
        ``hours=0`` (which sets the field back to ``None``).
        """
        hours_int = int(hours)
        state = self._load_state()
        now = float(self._clock())
        if hours_int <= 0:
            state["auto_check_paused_until"] = None
            new_until: float | None = None
        else:
            requested_until = now + 3600.0 * float(hours_int)
            existing = state.get("auto_check_paused_until")
            try:
                existing_f = float(existing) if existing is not None else 0.0
            except (TypeError, ValueError):
                existing_f = 0.0
            # Monotonic: never decrement an already-extended TTL.
            new_until = max(requested_until, existing_f)
            state["auto_check_paused_until"] = new_until
        # Record actor in last_failures audit slot? No — keep the
        # state shape clean; the audit row writes elsewhere.
        self._save_state(state)
        return {
            "auto_check_paused_until": new_until,
            "hours": hours_int,
            "by": by,
        }

    def release(self, *, by: str) -> dict[str, Any]:
        """Resume previously-paused clients. Idempotent.

        Returns ``{"released_clients": [...], "failures": [...],
        "engaged": False, "was_engaged": bool}``.
        """
        state = self._load_state()
        if not state.get("engaged"):
            return {
                "released_clients": [],
                "failures": [],
                "engaged": False,
                "was_engaged": False,
            }

        previously_paused = list(state.get("paused_clients") or [])
        released: list[str] = []
        failures: list[dict[str, Any]] = []
        # Build an id → adapter index so we only resume what was
        # actually paused (skip adapters that aren't in the
        # paused_clients list).
        index = {a.client_id: a for a in self._adapters}
        for client_id in previously_paused:
            adapter = index.get(client_id)
            if adapter is None:
                # Adapter no longer registered (config change between
                # engage and release). Best we can do is record + move on.
                failures.append({
                    "client": client_id,
                    "action": "resume",
                    "reason": "adapter_not_registered",
                })
                continue
            ok = self._per_client_resume(adapter)
            if ok:
                released.append(client_id)
            else:
                failures.append({
                    "client": client_id,
                    "action": "resume",
                })

        # Reset to canonical empty state — next engage starts clean.
        # ``last_failures`` keeps the resume-side errors for audit;
        # the engaged_at/engaged_by from the previous engage are
        # intentionally dropped (they're already in the audit-log
        # transitions stream).
        cleared = LOCKDOWN_STATE_FILE.empty_state()
        cleared["last_failures"] = failures
        self._save_state(cleared)
        return {
            "released_clients": released,
            "failures": failures,
            "engaged": False,
            "was_engaged": True,
            "release_actor": by,
        }

    # -- internals ---------------------------------------------------

    def _per_client_pause(self, adapter: DownloadClientLockdown) -> bool:
        try:
            return bool(adapter.pause_all())
        except (OSError, TimeoutError, ValueError) as exc:
            log_swallowed(
                exc, context=f"lockdown_pause_{adapter.client_id}",
            )
            _log.warning(
                "lockdown: adapter %s pause raised %s",
                adapter.client_id, exc,
            )
            return False

    def _per_client_resume(self, adapter: DownloadClientLockdown) -> bool:
        try:
            return bool(adapter.resume_all())
        except (OSError, TimeoutError, ValueError) as exc:
            log_swallowed(
                exc, context=f"lockdown_resume_{adapter.client_id}",
            )
            _log.warning(
                "lockdown: adapter %s resume raised %s",
                adapter.client_id, exc,
            )
            return False

    def _load_state(self) -> dict[str, Any]:
        path = self._state_path_fn()
        if not path.is_file():
            return LOCKDOWN_STATE_FILE.empty_state()
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning(
                "lockdown: state file at %s unreadable (%s); "
                "starting fresh", path, exc,
            )
            return LOCKDOWN_STATE_FILE.empty_state()
        if not isinstance(parsed, dict):
            _log.warning(
                "lockdown: state file at %s not a JSON object; "
                "starting fresh", path,
            )
            return LOCKDOWN_STATE_FILE.empty_state()
        # Merge into the canonical shape so missing keys default
        # rather than KeyError-ing the rule's evaluate().
        out = LOCKDOWN_STATE_FILE.empty_state()
        for key in out:
            if key in parsed:
                out[key] = parsed[key]
        # Coerce paused_clients to a clean list of strings.
        out["paused_clients"] = [
            str(c) for c in (out.get("paused_clients") or [])
            if isinstance(c, (str, int))
        ]
        # Validate trigger: drop bogus values rather than carry them
        # forward (the rule's evaluate switches on this string).
        if out.get("trigger") not in (None, _TRIGGER_AUTO, _TRIGGER_MANUAL):
            out["trigger"] = None
        return out

    def _save_state(self, state: Mapping[str, Any]) -> None:
        path = self._state_path_fn()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log_swallowed(exc, context="lockdown_state_mkdir")
            _log.warning(
                "lockdown: could not create state directory %s: %s",
                path.parent, exc,
            )
            return
        # Atomic replace via NamedTemporaryFile in the same dir so
        # ``os.replace`` stays on one filesystem.
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8",
                dir=str(path.parent),
                prefix=path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                _json_dump = json.dumps(dict(state), indent=2, sort_keys=True)
                tmp.write(_json_dump + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name
            os.replace(tmp_path, path)
        except OSError as exc:
            log_swallowed(exc, context="lockdown_state_save")
            _log.warning(
                "lockdown: failed to persist state at %s: %s", path, exc,
            )


__all__ = [
    "DownloadLockdownService",
    "LockdownStateFile",
    "LOCKDOWN_STATE_FILE",
]
