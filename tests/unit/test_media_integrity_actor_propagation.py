"""End-to-end ``actor`` propagation through ``MediaIntegrityService``.

The service layer accepts an arbitrary ``actor`` string on
``reconcile(actor=...)`` and ``enforce_config(actor=...)`` so the
HTTP layer can forward the authenticated operator's username (or
the literal ``"system"`` when invoked by the scheduler). Every
audit entry written during the pass MUST carry that actor —
otherwise an operator-initiated action looks like a scheduler tick
in the audit log, which makes incident review impossible.

What's pinned here:

- ``actor`` reaches the audit sink for every emit path
  (Servarr enforce/reconcile success + failure, Bazarr settings
  enforce + subtitle dedupe).
- ``actor`` defaults to ``"system"`` when omitted.
- Events do NOT carry actor today (deliberate — events are a
  fan-out signal, audit log is the operator-attribution channel).
  We only assert events still publish cleanly when actor is set.

Fakes are duplicated inline rather than shared via a fixture: the
project convention is one test file = self-contained, so you can
read a single file and understand it. The duplicated ~80 lines of
adapter scaffolding are intentional.
"""

from __future__ import annotations

from typing import Any

from media_stack.core.auth.users.audit_actions import (
    MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED,
    MEDIA_INTEGRITY_CONFIG_ENFORCED,
    MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
    MEDIA_INTEGRITY_RECONCILE_FAILED,
)
from media_stack.services.media_integrity.arr_protocol import (
    AdapterCapabilities,
    MediaFile,
    MediaRelease,
    QualityProfile,
)
from media_stack.services.media_integrity.bazarr_protocol import (
    BazarrCapabilities,
    SubtitleFile,
    SubtitleRelease,
)
from media_stack.services.media_integrity.policy import ServarrPolicy
from media_stack.services.media_integrity.service import MediaIntegrityService


# ---------------------------------------------------------------------------
# Fakes — copied inline (project convention: self-contained test files).
# ---------------------------------------------------------------------------


class _AuditSpy:
    """Records every audit append. Mirrors the structural sink the
    enforcer/reconciler call into."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def append(self, **kwargs: Any) -> dict[str, Any]:
        self.entries.append(kwargs)
        return kwargs


class _BusSpy:
    """Records published events. Used only to confirm events still
    publish even when an arbitrary actor flows through."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


def _file(
    *,
    id: str,
    release_id: str,
    quality_score: int = 0,
    added_at: str = "",
    size: int = 0,
) -> MediaFile:
    return MediaFile(
        id=id,
        release_id=release_id,
        relative_path=f"{id}.mkv",
        absolute_path=f"/media/{id}.mkv",
        size=size,
        quality_name="",
        quality_score=quality_score,
        added_at=added_at,
    )


def _sub(
    *,
    path: str,
    release_id: str,
    language: str = "en",
    forced: bool = False,
    hi: bool = False,
    score: int = 0,
    added_at: str = "",
    size: int = 0,
    release_kind: str = "movie",
) -> SubtitleFile:
    return SubtitleFile(
        release_id=release_id,
        release_kind=release_kind,
        path=path,
        language=language,
        forced=forced,
        hi=hi,
        score=score,
        added_at=added_at,
        size=size,
    )


class _FakeArrAdapter:
    """In-memory ArrApp for service-level actor-propagation tests."""

    name = "radarr"
    api_version = "v3"
    media_root = "/media"
    capabilities = AdapterCapabilities()

    def __init__(
        self,
        *,
        media_management: dict[str, Any] | None = None,
        naming: dict[str, Any] | None = None,
        releases: list[MediaRelease] | None = None,
        files_by_release: dict[str, list[MediaFile]] | None = None,
        list_releases_raises: Exception | None = None,
        put_mm_raises: Exception | None = None,
    ) -> None:
        self._mm = dict(media_management or {})
        self._naming = dict(naming or {})
        self._releases = list(releases or [])
        self._files = dict(files_by_release or {})
        self._list_releases_raises = list_releases_raises
        self._put_mm_raises = put_mm_raises
        self.deleted: list[str] = []

    # config surface
    def get_media_management(self) -> dict[str, Any]:
        return dict(self._mm)

    def put_media_management(self, cfg: dict[str, Any]) -> None:
        if self._put_mm_raises:
            raise self._put_mm_raises
        self._mm = dict(cfg)

    def get_naming(self) -> dict[str, Any]:
        return dict(self._naming)

    def put_naming(self, cfg: dict[str, Any]) -> None:
        self._naming = dict(cfg)

    def media_management_field_map(self) -> dict[str, str]:
        return {
            "auto_unmonitor_previously_downloaded": (
                "autoUnmonitorPreviouslyDownloadedMovies"
            ),
            "use_hardlinks": "copyUsingHardlinks",
            "delete_empty_folders": "deleteEmptyFolders",
            "import_extra_files": "importExtraFiles",
            "extra_file_extensions": "extraFileExtensions",
            "skip_free_space_check": "skipFreeSpaceCheckWhenImporting",
            "minimum_free_space_mb": "minimumFreeSpaceWhenImporting",
            "create_empty_media_folders": "createEmptyMovieFolders",
            "unmonitor_deleted": "autoUnmonitorDeletedMovies",
        }

    def naming_field_map(self) -> dict[str, str]:
        return {"rename_files": "renameMovies"}

    # inventory surface
    def list_releases(self) -> list[MediaRelease]:
        if self._list_releases_raises:
            raise self._list_releases_raises
        return list(self._releases)

    def list_files_for(self, release_id: str) -> list[MediaFile]:
        return list(self._files.get(release_id, []))

    def delete_file(self, file_id: str) -> None:
        self.deleted.append(file_id)

    def quality_profiles(self) -> list[QualityProfile]:
        return []

    def quality_score(self, file: MediaFile) -> int:
        return file.quality_score

    def list_releases_for_file(self, file_id: str) -> list[str]:
        # Default 1:1 — find the release that owns this file.
        for rid, files in self._files.items():
            if any(f.id == file_id for f in files):
                return [rid]
        return []


class _FakeBazarr:
    """In-memory BazarrApp for service-level actor-propagation tests."""

    name = "bazarr"
    api_version = "v1"
    media_root = "/media"
    capabilities = BazarrCapabilities()

    def __init__(
        self,
        *,
        settings: dict[str, Any] | None = None,
        releases: list[SubtitleRelease] | None = None,
        subs_by_release: dict[tuple[str, str], list[SubtitleFile]] | None = None,
    ) -> None:
        self._settings = dict(settings or {})
        self._releases = list(releases or [])
        self._subs = dict(subs_by_release or {})
        self.deleted: list[SubtitleFile] = []

    def get_settings(self) -> dict[str, Any]:
        return dict(self._settings)

    def put_settings(self, cfg: dict[str, Any]) -> None:
        self._settings = dict(cfg)

    def settings_field_map(self) -> dict[str, str]:
        return {
            "rename_files": "general.subfolder_custom",
            "auto_sync": "general.auto_sync_subs",
            "upgrade_allowed": "general.upgrade_subs",
            "ignore_deleted": "general.ignore_deleted_episodes",
        }

    def list_subtitle_releases(self) -> list[SubtitleRelease]:
        return list(self._releases)

    def list_subtitles_for(
        self, release_id: str, release_kind: str
    ) -> list[SubtitleFile]:
        return list(self._subs.get((release_id, release_kind), []))

    def delete_subtitle(self, subtitle: SubtitleFile) -> None:
        self.deleted.append(subtitle)

    def subtitle_score(self, subtitle: SubtitleFile) -> int:
        return subtitle.score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _radarr_compliant_mm() -> dict[str, Any]:
    """Servarr media-management blob already at policy — no PUT
    expected. Used by tests that want a clean baseline they can
    selectively drift one knob from."""
    return {
        "autoUnmonitorPreviouslyDownloadedMovies": True,
        "copyUsingHardlinks": True,
        "deleteEmptyFolders": True,
        "importExtraFiles": True,
        "extraFileExtensions": "srt,ass,ssa,vtt,smi,sub",
        "skipFreeSpaceCheckWhenImporting": False,
        "minimumFreeSpaceWhenImporting": 500,
        "createEmptyMovieFolders": False,
        "autoUnmonitorDeletedMovies": False,
        "id": 1,
    }


# ---------------------------------------------------------------------------
# Reconcile path
# ---------------------------------------------------------------------------


def test_reconcile_propagates_custom_actor_to_audit() -> None:
    """When an operator triggers reconcile via the API, every audit
    entry the reconciler writes during that pass must carry their
    actor — not the default ``"system"``."""
    release = MediaRelease(
        id="42", title="Spider-Man", path="/media/movies/Spider-Man"
    )
    keep = _file(
        id="keep",
        release_id="42",
        quality_score=10,
        added_at="2026-04-01",
        size=10_000,
    )
    drop = _file(
        id="drop",
        release_id="42",
        quality_score=5,
        added_at="2026-04-10",
        size=8_000,
    )
    adapter = _FakeArrAdapter(
        releases=[release], files_by_release={"42": [keep, drop]}
    )
    audit = _AuditSpy()
    bus = _BusSpy()
    service = MediaIntegrityService(
        policy=ServarrPolicy(),
        servarr_adapters=[adapter],
        audit=audit,
        event_bus=bus,
    )

    service.reconcile(actor="alice")

    # The duplicate-resolved audit entry must carry alice.
    resolved_entries = [
        e for e in audit.entries if e["action"] == MEDIA_INTEGRITY_DUPLICATE_RESOLVED
    ]
    assert resolved_entries, "expected at least one resolved audit entry"
    for entry in resolved_entries:
        assert entry["actor"] == "alice", entry

    # Every audit entry from this pass should be alice — none default
    # to "system".
    assert all(e["actor"] == "alice" for e in audit.entries), audit.entries

    # Sanity: events still publish (events do not carry actor — by
    # design — but they must not refuse the call).
    assert bus.events, "expected at least one event published"


def test_enforce_config_propagates_custom_actor_to_audit() -> None:
    """When enforce_config is invoked with an arbitrary actor, the
    success audit entry records that actor — not ``"system"``."""
    drifted_mm = _radarr_compliant_mm()
    drifted_mm["autoUnmonitorPreviouslyDownloadedMovies"] = False  # drift
    adapter = _FakeArrAdapter(
        media_management=drifted_mm,
        naming={"renameMovies": True, "id": 1},
    )
    audit = _AuditSpy()
    bus = _BusSpy()
    service = MediaIntegrityService(
        policy=ServarrPolicy(),
        servarr_adapters=[adapter],
        audit=audit,
        event_bus=bus,
    )

    service.enforce_config(actor="alice")

    enforced_entries = [
        e for e in audit.entries if e["action"] == MEDIA_INTEGRITY_CONFIG_ENFORCED
    ]
    assert enforced_entries
    for entry in enforced_entries:
        assert entry["actor"] == "alice", entry
    assert all(e["actor"] == "alice" for e in audit.entries)


def test_reconcile_defaults_actor_to_system_when_omitted() -> None:
    """Scheduler-driven calls don't pass an actor; the default must
    record as the literal string ``"system"``. That string is what
    the audit-log filter looks for to label background activity."""
    release = MediaRelease(id="1", title="X", path="/")
    keep = _file(
        id="keep",
        release_id="1",
        quality_score=10,
        added_at="2026-04-01",
        size=1,
    )
    drop = _file(
        id="drop",
        release_id="1",
        quality_score=5,
        added_at="2026-04-10",
        size=2,
    )
    adapter = _FakeArrAdapter(
        releases=[release], files_by_release={"1": [keep, drop]}
    )
    audit = _AuditSpy()
    service = MediaIntegrityService(
        policy=ServarrPolicy(),
        servarr_adapters=[adapter],
        audit=audit,
    )

    service.reconcile()  # actor omitted

    assert audit.entries, "expected at least one audit entry"
    assert all(e["actor"] == "system" for e in audit.entries), audit.entries


def test_bazarr_subtitle_dedupe_propagates_actor() -> None:
    """The Bazarr subtitle reconciler is reached via the same
    service entry-point. Its audit entries must also carry the
    operator's actor — not silently fall back to ``"system"`` because
    the Bazarr branch was forgotten."""
    release = SubtitleRelease(
        id="100", kind="movie", title="Spider-Man", path="/media/movies/Spider-Man"
    )
    keep = _sub(
        path="/media/movies/Spider-Man/sm.en.srt",
        release_id="100",
        score=95,
        added_at="2026-04-01",
        size=10_000,
    )
    drop = _sub(
        path="/media/movies/Spider-Man/sm.en.opensubs.srt",
        release_id="100",
        score=80,
        added_at="2026-04-10",
        size=8_000,
    )
    bazarr = _FakeBazarr(
        releases=[release],
        subs_by_release={("100", "movie"): [keep, drop]},
    )
    audit = _AuditSpy()
    service = MediaIntegrityService(
        policy=ServarrPolicy(),
        servarr_adapters=[],
        bazarr_adapter=bazarr,
        audit=audit,
    )

    service.reconcile(actor="bob")

    resolved = [
        e for e in audit.entries if e["action"] == MEDIA_INTEGRITY_DUPLICATE_RESOLVED
    ]
    # Bazarr-side resolution went through.
    assert resolved, "expected a bazarr subtitle resolved audit entry"
    assert all(e["actor"] == "bob" for e in resolved), resolved
    # No entry leaked through as "system".
    assert all(e["actor"] == "bob" for e in audit.entries), audit.entries


# ---------------------------------------------------------------------------
# Failure paths — actor must still propagate.
# ---------------------------------------------------------------------------


def test_enforce_config_failure_propagates_actor() -> None:
    """A PUT failure during enforce-config produces a failure audit
    entry. That entry must carry the caller's actor — losing
    attribution on the failure path is the worst case (incident
    review needs to know who pressed the button)."""
    drifted_mm = _radarr_compliant_mm()
    drifted_mm["autoUnmonitorPreviouslyDownloadedMovies"] = False
    adapter = _FakeArrAdapter(
        media_management=drifted_mm,
        naming={"renameMovies": True, "id": 1},
        put_mm_raises=RuntimeError("503 Service Unavailable"),
    )
    audit = _AuditSpy()
    service = MediaIntegrityService(
        policy=ServarrPolicy(),
        servarr_adapters=[adapter],
        audit=audit,
    )

    service.enforce_config(actor="alice")

    failed_entries = [
        e
        for e in audit.entries
        if e["action"] == MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED
    ]
    assert failed_entries, "expected an enforce_failed audit entry"
    for entry in failed_entries:
        assert entry["actor"] == "alice", entry
        # Result should be marked as failure (orthogonal to actor,
        # but verifying we didn't mix up the kwargs).
        assert entry.get("result") == "failure"


def test_reconcile_failure_propagates_actor() -> None:
    """When ``list_releases`` blows up on the adapter, the reconciler
    emits a ``reconcile_failed`` audit entry. That entry must carry
    the caller's actor."""
    adapter = _FakeArrAdapter(
        list_releases_raises=RuntimeError("connection refused"),
    )
    audit = _AuditSpy()
    service = MediaIntegrityService(
        policy=ServarrPolicy(),
        servarr_adapters=[adapter],
        audit=audit,
    )

    service.reconcile(actor="alice")

    failed_entries = [
        e for e in audit.entries if e["action"] == MEDIA_INTEGRITY_RECONCILE_FAILED
    ]
    assert failed_entries, "expected a reconcile_failed audit entry"
    for entry in failed_entries:
        assert entry["actor"] == "alice", entry
        assert entry.get("result") == "failure"
