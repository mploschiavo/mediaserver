"""Top-level ``MediaIntegrityService`` — the single object the API
layer and the scheduler consume.

Combines:
- ``ServarrConfigEnforcer`` — applies mediamanagement + naming policy
  to Radarr/Sonarr/Lidarr/Readarr.
- ``MediaIntegrityReconciler`` — heals duplicate files on the Servarr
  family.
- ``BazarrSettingsEnforcer`` — applies the Bazarr settings slice.
- ``BazarrSubtitleReconciler`` — heals duplicate subtitles.

Holds the most-recent report of each pass in memory so the
``GET /api/media-integrity/status`` endpoint can render the UI card
without re-running the work. The status cache is the only mutable
state on the service and is guarded by an ``RLock``.

Concurrency model
-----------------
``enforce_config`` and ``reconcile`` are heavy passes that hit every
adapter's HTTP API. We refuse to run two of the same operation in
parallel — a duplicate trigger from a double-clicked UI button or a
scheduler tick coinciding with an admin trigger raises
``MediaIntegrityInProgress`` so the handler can map it to a 409. Each
operation family has its OWN mutex: a slow reconcile must not block a
config enforcement, and vice versa.

``resolve_review`` is operator-driven manual reconciliation; it shares
the reconcile mutex because it mutates the same library state.
"""

from __future__ import annotations

import threading
from media_stack.core.logging_utils import log_swallowed
from dataclasses import dataclass, field
from typing import Any

from media_stack.core.auth.users.audit_actions import (
    MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
)
from media_stack.core.events import EventBus, MediaIntegrityDuplicateResolved
from media_stack.core.time_utils import utcnow_iso
from media_stack.services.media_integrity.arr_protocol import ArrApp
from media_stack.services.media_integrity.bazarr_protocol import BazarrApp
from media_stack.services.media_integrity.enforcer import (
    EnforceReport,
    EnforceResult,
    ServarrConfigEnforcer,
)
from media_stack.services.media_integrity.policy import ServarrPolicy
from media_stack.services.media_integrity.reconciler import (
    AdapterReconcileResult,
    MediaIntegrityReconciler,
    ReconcileReport,
)
from media_stack.services.media_integrity.subtitle_reconciler import (
    BazarrEnforceReport,
    BazarrReconcileReport,
    BazarrSettingsEnforcer,
    BazarrSubtitleReconciler,
)


class MediaIntegrityInProgress(Exception):
    """A reconcile/enforce pass is already running for this op family.

    Callers (the API handler) translate this to HTTP 409 Conflict.
    Carrying the operation name lets observability distinguish a
    "reconcile blocked by reconcile" from "enforce blocked by enforce"
    without re-deriving it from the call site.
    """

    def __init__(self, op: str) -> None:
        super().__init__(f"{op} already in progress")
        self.op = op


@dataclass
class _LastRun:
    ts: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


class MediaIntegrityService:
    """Orchestrator for config-enforce + reconcile across every *arr
    and Bazarr.

    Construction is dependency-injected: the caller supplies the
    policy + adapter list + (optional) Bazarr adapter + (optional)
    audit + event bus. Tests pass fakes; production factories wire
    real adapters from the service registry.
    """

    def __init__(
        self,
        *,
        policy: ServarrPolicy,
        servarr_adapters: list[ArrApp] | None = None,
        bazarr_adapter: BazarrApp | None = None,
        audit: Any = None,
        event_bus: EventBus | None = None,
        missing_keys: list[str] | None = None,
    ) -> None:
        self._policy = policy
        self._servarr_adapters = list(servarr_adapters or [])
        self._bazarr_adapter = bazarr_adapter
        self._audit = audit
        self._event_bus = event_bus
        self._missing_keys = list(missing_keys or [])
        self._enforcer = ServarrConfigEnforcer(
            policy=policy, audit=audit, event_bus=event_bus
        )
        self._reconciler = MediaIntegrityReconciler(
            audit=audit, event_bus=event_bus
        )
        self._bazarr_settings_enforcer = BazarrSettingsEnforcer(
            policy=policy, audit=audit, event_bus=event_bus
        )
        self._bazarr_reconciler = BazarrSubtitleReconciler(
            audit=audit, event_bus=event_bus
        )
        self._lock = threading.RLock()
        # Per-family mutexes — see module docstring.
        self._enforce_mutex = threading.Lock()
        self._reconcile_mutex = threading.Lock()
        self._last_enforce: _LastRun = _LastRun()
        self._last_reconcile: _LastRun = _LastRun()
        self._current_progress: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def enforce_config(self, *, actor: str = "system") -> dict[str, Any]:
        """Apply policy to every adapter (Servarr + Bazarr). Returns
        a JSON-serialisable summary."""
        if not self._enforce_mutex.acquire(blocking=False):
            raise MediaIntegrityInProgress("enforce_config")
        try:
            self._set_progress({
                "op": "enforce_config",
                "started_at": utcnow_iso(),
                "phase": "running",
                "current": None,
                "total": None,
            })
            servarr_report = self._enforcer.apply(
                self._servarr_adapters, actor=actor
            )
            bazarr_report: BazarrEnforceReport | None = None
            if self._bazarr_adapter is not None:
                bazarr_report = self._bazarr_settings_enforcer.apply(
                    self._bazarr_adapter, actor=actor
                )
            payload = _format_enforce(servarr_report, bazarr_report)
            with self._lock:
                self._last_enforce = _LastRun(ts=utcnow_iso(), detail=payload)
            return payload
        finally:
            self._clear_progress()
            self._enforce_mutex.release()

    def reconcile(
        self, *, actor: str = "system", dry_run: bool = False,
    ) -> dict[str, Any]:
        """Heal duplicate files + subtitles. Returns a JSON summary.

        ``dry_run=True`` walks the same winner-picking logic but
        does not call ``adapter.delete_file``/``delete_subtitle``;
        the report shows what WOULD have been deleted. Used by the
        UI's "Dry run" checkbox before an operator commits to a
        real reconcile."""
        if not self._reconcile_mutex.acquire(blocking=False):
            raise MediaIntegrityInProgress("reconcile")
        try:
            self._set_progress({
                "op": "reconcile",
                "started_at": utcnow_iso(),
                "phase": "running",
                "current": None,
                "total": None,
                "dry_run": dry_run,
            })
            servarr_report = self._reconciler.reconcile(
                self._servarr_adapters, actor=actor, dry_run=dry_run,
            )
            bazarr_report: BazarrReconcileReport | None = None
            if self._bazarr_adapter is not None:
                bazarr_report = self._bazarr_reconciler.reconcile(
                    self._bazarr_adapter, actor=actor, dry_run=dry_run,
                )
            payload = _format_reconcile(servarr_report, bazarr_report)
            payload["dry_run"] = dry_run
            with self._lock:
                # A dry-run preview does NOT update last_reconcile —
                # status reflects only real passes.
                if not dry_run:
                    self._last_reconcile = _LastRun(
                        ts=utcnow_iso(), detail=payload,
                    )
            return payload
        finally:
            self._clear_progress()
            self._reconcile_mutex.release()

    def resolve_review(
        self,
        app: str,
        release_id: str,
        *,
        winner_file_id: str | None = None,
        winner_sub_path: str | None = None,
        release_kind: str | None = None,
        language: str | None = None,
        forced: bool = False,
        hi: bool = False,
        actor: str = "system",
    ) -> dict[str, Any]:
        """Operator-driven duplicate resolution.

        Servarr branch (winner_file_id supplied): delete every other
        file backing ``release_id`` on the named app. Bazarr branch
        (winner_sub_path supplied): delete every other subtitle in the
        ``(language, forced, hi)`` group attached to
        ``(release_id, release_kind)``.

        Shares the reconcile mutex — operator-driven and scheduler-
        driven reconciliation must not race against each other.
        """
        if winner_file_id and winner_sub_path:
            raise ValueError(
                "winner_file_id and winner_sub_path are mutually exclusive",
            )
        if not winner_file_id and not winner_sub_path:
            raise ValueError(
                "winner_file_id or winner_sub_path required",
            )
        if not self._reconcile_mutex.acquire(blocking=False):
            raise MediaIntegrityInProgress("reconcile")
        try:
            if winner_file_id:
                return self._resolve_review_servarr(
                    app, release_id, winner_file_id, actor=actor,
                )
            assert winner_sub_path is not None  # narrowed by branch above
            if not release_kind:
                raise ValueError("release_kind required for subtitle resolution")
            if not language:
                raise ValueError("language required for subtitle resolution")
            return self._resolve_review_bazarr(
                app,
                release_id,
                winner_sub_path,
                release_kind=release_kind,
                language=language,
                forced=forced,
                hi=hi,
                actor=actor,
            )
        finally:
            self._reconcile_mutex.release()

    def status(self) -> dict[str, Any]:
        """Snapshot of last pass outcomes — consumed by
        ``GET /api/media-integrity/status``."""
        with self._lock:
            return {
                "last_enforce": {
                    "ts": self._last_enforce.ts,
                    "detail": dict(self._last_enforce.detail),
                },
                "last_reconcile": {
                    "ts": self._last_reconcile.ts,
                    "detail": dict(self._last_reconcile.detail),
                },
                "policy_version": self._policy.version,
                "servarr_adapters": tuple(a.name for a in self._servarr_adapters),
                "bazarr_present": self._bazarr_adapter is not None,
                "missing_api_keys": list(self._missing_keys),
            }

    def get_progress(self) -> dict[str, Any]:
        """Snapshot of the in-flight operation, if any.

        Returns ``{"in_progress": False}`` when idle so the UI's poll
        loop has a cheap, stable shape to branch on. When an op is
        running, returns the original progress dict augmented with
        ``in_progress: True``.
        """
        with self._lock:
            if self._current_progress is None:
                return {"in_progress": False}
            return {"in_progress": True, **dict(self._current_progress)}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _set_progress(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._current_progress = dict(snapshot)

    def _clear_progress(self) -> None:
        with self._lock:
            self._current_progress = None

    def _resolve_review_servarr(
        self,
        app: str,
        release_id: str,
        winner_file_id: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        adapter = self._find_servarr(app)
        if adapter is None:
            raise ValueError("unknown app")
        files = adapter.list_files_for(release_id)
        winner = next((f for f in files if f.id == winner_file_id), None)
        if winner is None:
            raise ValueError("winner_file_id not present in release")
        losers = [f for f in files if f.id != winner_file_id]
        deleted_ids: list[str] = []
        bytes_freed = 0
        for loser in losers:
            adapter.delete_file(loser.id)
            deleted_ids.append(loser.id)
            bytes_freed += int(getattr(loser, "size", 0) or 0)
        # Title is best-effort — release listing isn't free.
        release_title = ""
        for release in self._safe_list_releases(adapter):
            if release.id == release_id:
                release_title = release.title
                break
        self._emit_resolved_event(
            app=app,
            release_id=release_id,
            release_title=release_title,
            winner_file_id=winner_file_id,
            loser_ids=deleted_ids,
            bytes_freed=bytes_freed,
            actor=actor,
        )
        return {
            "app": app,
            "release_id": release_id,
            "deleted_count": len(deleted_ids),
            "bytes_freed": bytes_freed,
        }

    def _resolve_review_bazarr(
        self,
        app: str,
        release_id: str,
        winner_sub_path: str,
        *,
        release_kind: str,
        language: str,
        forced: bool,
        hi: bool,
        actor: str,
    ) -> dict[str, Any]:
        if self._bazarr_adapter is None or app != self._bazarr_adapter.name:
            raise ValueError("unknown app")
        adapter = self._bazarr_adapter
        subs = adapter.list_subtitles_for(release_id, release_kind)
        in_group = [
            s
            for s in subs
            if s.language == language and bool(s.forced) == forced
            and bool(s.hi) == hi
        ]
        if not any(s.path == winner_sub_path for s in in_group):
            raise ValueError("winner_sub_path not present in group")
        losers = [s for s in in_group if s.path != winner_sub_path]
        bytes_freed = 0
        deleted = 0
        for loser in losers:
            adapter.delete_subtitle(loser)
            deleted += 1
            bytes_freed += int(getattr(loser, "size", 0) or 0)
        self._emit_resolved_event(
            app=app,
            release_id=release_id,
            release_title="",
            winner_file_id=winner_sub_path,
            loser_ids=[loser.path for loser in losers],
            bytes_freed=bytes_freed,
            actor=actor,
        )
        return {
            "app": app,
            "release_id": release_id,
            "deleted_count": deleted,
            "bytes_freed": bytes_freed,
        }

    def _find_servarr(self, app: str) -> ArrApp | None:
        for adapter in self._servarr_adapters:
            if adapter.name == app:
                return adapter
        return None

    def _safe_list_releases(self, adapter: ArrApp) -> list:
        try:
            return list(adapter.list_releases())
        except Exception:
            return []

    def _emit_resolved_event(
        self,
        *,
        app: str,
        release_id: str,
        release_title: str,
        winner_file_id: str,
        loser_ids: list[str],
        bytes_freed: int,
        actor: str,
    ) -> None:
        if self._event_bus is not None:
            try:
                self._event_bus.publish(
                    MediaIntegrityDuplicateResolved(
                        app=app,
                        release_id=release_id,
                        release_title=release_title,
                        winner_file_id=winner_file_id,
                        loser_file_ids=tuple(loser_ids),
                        total_bytes_freed=bytes_freed,
                    )
                )
            except Exception as exc:
                log_swallowed(exc)
        if self._audit is not None:
            try:
                self._audit.append(
                    actor=actor,
                    action=MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
                    target=f"{app}:{release_id}",
                    detail={
                        "release_title": release_title,
                        "winner_file_id": winner_file_id,
                        "loser_file_ids": list(loser_ids),
                        "bytes_freed": bytes_freed,
                        "operator_resolved": True,
                    },
                )
            except Exception as exc:
                log_swallowed(exc)


# ---------------------------------------------------------------------------
# JSON projections — produce stable shapes for the UI + tests
# ---------------------------------------------------------------------------


def _format_enforce(
    servarr: EnforceReport,
    bazarr: BazarrEnforceReport | None,
) -> dict[str, Any]:
    return {
        "servarr": {
            "results": [_format_enforce_result(r) for r in servarr.results],
            "total_fields_changed": servarr.total_fields_changed,
            "total_failures": servarr.total_failures,
        },
        "bazarr": _format_bazarr_enforce(bazarr),
    }


def _format_enforce_result(r: EnforceResult) -> dict[str, Any]:
    return {
        "app": r.app,
        "mediamanagement_changed_fields": list(r.mediamanagement_changed_fields),
        "naming_changed_fields": list(r.naming_changed_fields),
        "failures": list(r.failures),
    }


def _format_bazarr_enforce(r: BazarrEnforceReport | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "changed_paths": list(r.changed_paths),
        "failures": list(r.failures),
    }


def _format_reconcile(
    servarr: ReconcileReport,
    bazarr: BazarrReconcileReport | None,
) -> dict[str, Any]:
    return {
        "servarr": {
            "results": [_format_reconcile_result(r) for r in servarr.results],
            "total_resolved": servarr.total_resolved,
            "total_needs_review": servarr.total_needs_review,
            "total_failures": servarr.total_failures,
            "total_bytes_freed": servarr.total_bytes_freed,
        },
        "bazarr": _format_bazarr_reconcile(bazarr),
    }


def _format_reconcile_result(r: AdapterReconcileResult) -> dict[str, Any]:
    return {
        "app": r.app,
        "resolved": [
            {
                "release_id": x.release_id,
                "release_title": x.release_title,
                "winner_file_id": x.winner_file_id,
                "loser_file_ids": list(x.loser_file_ids),
                "bytes_freed": x.bytes_freed,
            }
            for x in r.resolved
        ],
        "needs_review": [
            {
                "release_id": x.release_id,
                "release_title": x.release_title,
                "candidate_file_ids": list(x.candidate_file_ids),
            }
            for x in r.needs_review
        ],
        "failures": list(r.failures),
    }


def _format_bazarr_reconcile(r: BazarrReconcileReport | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "resolved": [
            {
                "release_id": x.release_id,
                "release_kind": x.release_kind,
                "release_title": x.release_title,
                "language": x.language,
                "forced": x.forced,
                "hi": x.hi,
                "winner_path": x.winner_path,
                "loser_paths": list(x.loser_paths),
                "bytes_freed": x.bytes_freed,
            }
            for x in r.resolved
        ],
        "needs_review": [
            {
                "release_id": x.release_id,
                "release_kind": x.release_kind,
                "release_title": x.release_title,
                "language": x.language,
                "forced": x.forced,
                "hi": x.hi,
                "candidate_paths": list(x.candidate_paths),
            }
            for x in r.needs_review
        ],
        "failures": list(r.failures),
        "total_bytes_freed": r.total_bytes_freed,
    }


__all__ = [
    "MediaIntegrityInProgress",
    "MediaIntegrityService",
]
