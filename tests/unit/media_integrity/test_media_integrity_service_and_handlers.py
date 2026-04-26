"""Tests for ``MediaIntegrityService`` orchestrator and the
``MediaIntegrityHandlers`` HTTP dispatcher."""

from __future__ import annotations

import threading
import time
from http import HTTPStatus
from typing import Any

import pytest

from media_stack.api.services.media_integrity_handlers import MediaIntegrityHandlers
from media_stack.core.auth.authz import Actor
from media_stack.core.auth.idempotency_cache import IdempotencyCache
from media_stack.core.events import EventBus, MediaIntegrityDuplicateResolved
from media_stack.services.media_integrity.arr_protocol import (
    AdapterCapabilities,
    MediaFile,
    MediaRelease,
)
from media_stack.services.media_integrity.bazarr_protocol import (
    BazarrCapabilities,
    SubtitleFile,
    SubtitleRelease,
)
from media_stack.services.media_integrity.policy import ServarrPolicy
from media_stack.services.media_integrity.service import (
    MediaIntegrityInProgress,
    MediaIntegrityService,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeArrAdapter:
    name = "radarr"
    api_version = "v3"
    media_root = "/media"
    capabilities = AdapterCapabilities()

    def __init__(
        self,
        *,
        mm: dict[str, Any] | None = None,
        naming: dict[str, Any] | None = None,
        releases: list[MediaRelease] | None = None,
        files_by_release: dict[str, list[MediaFile]] | None = None,
    ) -> None:
        self._mm = dict(mm or {})
        self._naming = dict(naming or {})
        self._releases = list(releases or [])
        self._files = dict(files_by_release or {})
        self.deleted: list[str] = []
        self.put_calls: list[tuple[str, dict]] = []

    def get_media_management(self) -> dict[str, Any]:
        return dict(self._mm)

    def put_media_management(self, cfg: dict[str, Any]) -> None:
        self.put_calls.append(("mm", dict(cfg)))
        self._mm = dict(cfg)

    def get_naming(self) -> dict[str, Any]:
        return dict(self._naming)

    def put_naming(self, cfg: dict[str, Any]) -> None:
        self.put_calls.append(("naming", dict(cfg)))
        self._naming = dict(cfg)

    def list_releases(self) -> list[MediaRelease]:
        return list(self._releases)

    def list_files_for(self, release_id: str) -> list[MediaFile]:
        return list(self._files.get(release_id, []))

    def delete_file(self, file_id: str) -> None:
        self.deleted.append(file_id)

    def quality_profiles(self) -> list:
        return []

    def list_releases_for_file(self, file_id: str) -> list[str]:
        for rid, files in self._files.items():
            if any(f.id == file_id for f in files):
                return [rid]
        return []

    def quality_score(self, file: MediaFile) -> int:
        return file.quality_score

    def media_management_field_map(self) -> dict[str, str]:
        return {
            "auto_unmonitor_previously_downloaded": "autoUnmonitorPreviouslyDownloadedMovies",
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


class _FakeBazarr:
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
        self._settings = dict(settings or {"general": {}})
        self._releases = list(releases or [])
        self._subs = dict(subs_by_release or {})
        self.put_calls: list[dict] = []
        self.deleted: list[SubtitleFile] = []

    def get_settings(self) -> dict[str, Any]:
        return _deep_copy(self._settings)

    def put_settings(self, cfg: dict[str, Any]) -> None:
        self.put_calls.append(_deep_copy(cfg))
        self._settings = _deep_copy(cfg)

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


def _deep_copy(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_copy(v) for v in obj]
    return obj


class _Headers(dict):
    """``dict``-shaped fake of ``BaseHTTPRequestHandler.headers``.

    Real headers expose ``.get(name, default)``; a plain dict already
    does so we just inherit. Subclassing keeps construction explicit
    in tests."""


class _ResponseSpy:
    """Stands in for ``ControllerAPIHandler``. Captures responses."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.responses: list[tuple[int, dict]] = []
        self.path = ""
        self.headers = _Headers(headers or {})

    def _json_response(self, status: int, payload: dict) -> None:
        self.responses.append((int(status), dict(payload)))


# ---------------------------------------------------------------------------
# MediaIntegrityService
# ---------------------------------------------------------------------------


def test_service_status_initial_state() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    status = svc.status()
    assert status["last_enforce"]["ts"] == ""
    assert status["last_reconcile"]["ts"] == ""
    assert status["policy_version"] == 1
    assert status["servarr_adapters"] == ()
    assert status["bazarr_present"] is False


def test_service_enforce_config_records_last_run() -> None:
    adapter = _FakeArrAdapter(
        mm={"autoUnmonitorPreviouslyDownloadedMovies": False, "id": 1},
        naming={"renameMovies": True, "id": 1},
    )
    svc = MediaIntegrityService(
        policy=ServarrPolicy(), servarr_adapters=[adapter]
    )
    payload = svc.enforce_config()
    assert payload["servarr"]["total_fields_changed"] >= 1
    assert payload["bazarr"] is None
    status = svc.status()
    assert status["last_enforce"]["ts"] != ""


def test_service_enforce_config_includes_bazarr_when_present() -> None:
    arr = _FakeArrAdapter(
        mm={"autoUnmonitorPreviouslyDownloadedMovies": True, "id": 1},
        naming={"renameMovies": True, "id": 1},
    )
    bz = _FakeBazarr(
        settings={
            "general": {
                "subfolder_custom": False,
                "auto_sync_subs": False,
                "upgrade_subs": False,
                "ignore_deleted_episodes": False,
            }
        }
    )
    svc = MediaIntegrityService(
        policy=ServarrPolicy(), servarr_adapters=[arr], bazarr_adapter=bz
    )
    payload = svc.enforce_config()
    assert payload["bazarr"] is not None
    assert payload["bazarr"]["changed_paths"]
    assert svc.status()["bazarr_present"] is True


def test_service_reconcile_resolves_dupes_across_servarr_and_bazarr() -> None:
    release = MediaRelease(id="42", title="Spider-Man", path="/m/sm")
    keep = MediaFile(
        id="keep", release_id="42", relative_path="k.mkv", absolute_path="/m/sm/k.mkv",
        size=10, quality_name="WEBDL-1080p", quality_score=10,
        added_at="2026-04-01",
    )
    drop = MediaFile(
        id="drop", release_id="42", relative_path="d.mkv", absolute_path="/m/sm/d.mkv",
        size=5, quality_name="HDTV-1080p", quality_score=5,
        added_at="2026-04-10",
    )
    arr = _FakeArrAdapter(releases=[release], files_by_release={"42": [keep, drop]})

    sub_release = SubtitleRelease(id="100", kind="movie", title="Spider-Man", path="/")
    sub_keep = SubtitleFile(release_id="100", release_kind="movie", path="/k.srt", language="en", score=95, added_at="2026-04-01")
    sub_drop = SubtitleFile(release_id="100", release_kind="movie", path="/d.srt", language="en", score=80, added_at="2026-04-10", size=8)
    bz = _FakeBazarr(
        releases=[sub_release],
        subs_by_release={("100", "movie"): [sub_keep, sub_drop]},
    )

    svc = MediaIntegrityService(
        policy=ServarrPolicy(), servarr_adapters=[arr], bazarr_adapter=bz
    )
    payload = svc.reconcile()
    assert payload["servarr"]["total_resolved"] == 1
    assert payload["bazarr"]["total_bytes_freed"] == 8
    assert arr.deleted == ["drop"]
    assert bz.deleted == [sub_drop]


# ---------------------------------------------------------------------------
# MediaIntegrityHandlers
# ---------------------------------------------------------------------------


def _make_actor(*, admin: bool, label: str = "alice") -> Actor:
    return Actor(username=label, is_admin=admin)


def test_handler_matches_routes() -> None:
    h = MediaIntegrityHandlers()
    assert h.matches_get("/api/media-integrity/status")
    assert not h.matches_get("/api/media-integrity/reconcile")
    assert h.matches_post("/api/media-integrity/reconcile")
    assert h.matches_post("/api/media-integrity/enforce-config")
    assert not h.matches_post("/api/other")


def test_handler_returns_503_when_service_not_set() -> None:
    h = MediaIntegrityHandlers()
    handler = _ResponseSpy()
    h.dispatch_get(handler, "/api/media-integrity/status", _make_actor(admin=True))
    assert handler.responses[0][0] == HTTPStatus.SERVICE_UNAVAILABLE


def test_handler_post_returns_503_when_service_not_set() -> None:
    h = MediaIntegrityHandlers()
    handler = _ResponseSpy()
    h.dispatch_post(
        handler,
        "/api/media-integrity/reconcile",
        {},
        _make_actor(admin=True),
    )
    assert handler.responses[0][0] == HTTPStatus.SERVICE_UNAVAILABLE


def test_handler_status_returns_unauthorized_for_unauthenticated() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    actor = Actor(username="", is_admin=False)
    h.dispatch_get(handler, "/api/media-integrity/status", actor)
    assert handler.responses[0][0] == HTTPStatus.UNAUTHORIZED


def test_handler_status_succeeds_for_authenticated_non_admin() -> None:
    """Non-admin observers can read the status card."""
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    actor = _make_actor(admin=False, label="observer")
    h.dispatch_get(handler, "/api/media-integrity/status", actor)
    assert handler.responses[0][0] == HTTPStatus.OK
    body = handler.responses[0][1]
    assert "policy_version" in body


def test_handler_post_reconcile_requires_admin() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    actor = _make_actor(admin=False)
    h.dispatch_post(
        handler, "/api/media-integrity/reconcile", {}, actor,
    )
    assert handler.responses[0][0] == HTTPStatus.FORBIDDEN


def test_handler_post_reconcile_runs_for_admin() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    h.dispatch_post(
        handler,
        "/api/media-integrity/reconcile",
        {},
        _make_actor(admin=True),
    )
    assert handler.responses[0][0] == HTTPStatus.OK
    assert "servarr" in handler.responses[0][1]


def test_handler_post_enforce_runs_for_admin() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    h.dispatch_post(
        handler,
        "/api/media-integrity/enforce-config",
        {},
        _make_actor(admin=True),
    )
    assert handler.responses[0][0] == HTTPStatus.OK
    assert "servarr" in handler.responses[0][1]


def test_handler_post_unknown_path_returns_404() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    h.dispatch_post(
        handler, "/api/media-integrity/something-else", {},
        _make_actor(admin=True),
    )
    assert handler.responses[0][0] == HTTPStatus.NOT_FOUND


def test_handler_get_unknown_path_returns_404() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    h.dispatch_get(
        handler, "/api/media-integrity/missing", _make_actor(admin=True)
    )
    assert handler.responses[0][0] == HTTPStatus.NOT_FOUND


def test_handler_set_service_swap() -> None:
    h = MediaIntegrityHandlers()
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h.set_service(svc)
    handler = _ResponseSpy()
    h.dispatch_get(
        handler, "/api/media-integrity/status", _make_actor(admin=True),
    )
    assert handler.responses[0][0] == HTTPStatus.OK
    h.set_service(None)
    handler = _ResponseSpy()
    h.dispatch_get(
        handler, "/api/media-integrity/status", _make_actor(admin=True),
    )
    assert handler.responses[0][0] == HTTPStatus.SERVICE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Concurrency mutex (Task 2)
# ---------------------------------------------------------------------------


class _BlockingReconciler:
    """Drop-in for ``MediaIntegrityReconciler`` that blocks on an
    event so tests can race a second call against the first."""

    def __init__(self, gate: threading.Event) -> None:
        self._gate = gate

    def reconcile(self, adapters, *, actor="system", dry_run=False):
        from media_stack.services.media_integrity.reconciler import (
            ReconcileReport,
        )
        self._gate.wait(timeout=5.0)
        return ReconcileReport()


class _BlockingEnforcer:
    def __init__(self, gate: threading.Event) -> None:
        self._gate = gate

    def apply(self, adapters, *, actor="system"):
        from media_stack.services.media_integrity.enforcer import (
            EnforceReport,
        )
        self._gate.wait(timeout=5.0)
        return EnforceReport()


def test_service_rejects_concurrent_reconcile() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    gate = threading.Event()
    svc._reconciler = _BlockingReconciler(gate)
    holder: dict[str, Any] = {}

    def runner() -> None:
        holder["result"] = svc.reconcile()

    t = threading.Thread(target=runner)
    t.start()
    # Spin briefly until the worker has acquired the mutex.
    deadline = time.monotonic() + 1.0
    while not svc._reconcile_mutex.locked() and time.monotonic() < deadline:
        time.sleep(0.005)
    with pytest.raises(MediaIntegrityInProgress) as exc_info:
        svc.reconcile()
    assert exc_info.value.op == "reconcile"
    gate.set()
    t.join(timeout=2.0)
    assert "result" in holder


def test_service_rejects_concurrent_enforce() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    gate = threading.Event()
    svc._enforcer = _BlockingEnforcer(gate)

    def runner() -> None:
        svc.enforce_config()

    t = threading.Thread(target=runner)
    t.start()
    deadline = time.monotonic() + 1.0
    while not svc._enforce_mutex.locked() and time.monotonic() < deadline:
        time.sleep(0.005)
    with pytest.raises(MediaIntegrityInProgress) as exc_info:
        svc.enforce_config()
    assert exc_info.value.op == "enforce_config"
    gate.set()
    t.join(timeout=2.0)


def test_enforce_not_blocked_by_concurrent_reconcile() -> None:
    """Enforce + reconcile have INDEPENDENT mutexes."""
    svc = MediaIntegrityService(policy=ServarrPolicy())
    gate = threading.Event()
    svc._reconciler = _BlockingReconciler(gate)

    def runner() -> None:
        svc.reconcile()

    t = threading.Thread(target=runner)
    t.start()
    deadline = time.monotonic() + 1.0
    while not svc._reconcile_mutex.locked() and time.monotonic() < deadline:
        time.sleep(0.005)
    # Enforce should run cleanly even though reconcile is still in flight.
    payload = svc.enforce_config()
    assert "servarr" in payload
    gate.set()
    t.join(timeout=2.0)


def test_handler_maps_in_progress_to_409() -> None:
    class _RaisingService:
        def reconcile(self, *, actor="system", dry_run=False):
            raise MediaIntegrityInProgress("reconcile")

        def enforce_config(self, *, actor="system"):
            raise MediaIntegrityInProgress("enforce_config")

        def status(self):
            return {}

        def get_progress(self):
            return {"in_progress": False}

    h = MediaIntegrityHandlers(service=_RaisingService())  # type: ignore[arg-type]
    handler = _ResponseSpy()
    h.dispatch_post(
        handler, "/api/media-integrity/reconcile", {}, _make_actor(admin=True),
    )
    assert handler.responses[0][0] == HTTPStatus.CONFLICT
    assert handler.responses[0][1] == {"error": "already in progress"}


# ---------------------------------------------------------------------------
# Idempotency (Task 1)
# ---------------------------------------------------------------------------


def test_handler_caches_response_under_idempotency_key() -> None:
    """Second POST with the same key returns the cached payload and
    skips side effects entirely."""
    from media_stack.services.media_integrity.reconciler import (
        ReconcileReport,
    )

    class _CountingReconciler:
        def __init__(self) -> None:
            self.calls = 0

        def reconcile(self, adapters, *, actor="system", dry_run=False):
            self.calls += 1
            return ReconcileReport()

    cache = IdempotencyCache(ttl_seconds=60, max_entries=8)
    svc = MediaIntegrityService(policy=ServarrPolicy())
    counter = _CountingReconciler()
    svc._reconciler = counter
    h = MediaIntegrityHandlers(service=svc, cache=cache)

    handler1 = _ResponseSpy(headers={"Idempotency-Key": "abc-123"})
    h.dispatch_post(
        handler1, "/api/media-integrity/reconcile", {},
        _make_actor(admin=True),
    )
    assert handler1.responses[0][0] == HTTPStatus.OK
    first_payload = handler1.responses[0][1]
    assert counter.calls == 1

    handler2 = _ResponseSpy(headers={"Idempotency-Key": "abc-123"})
    h.dispatch_post(
        handler2, "/api/media-integrity/reconcile", {},
        _make_actor(admin=True),
    )
    assert handler2.responses[0][0] == HTTPStatus.OK
    assert handler2.responses[0][1] == first_payload
    # Critically: the underlying reconcile did NOT run a second time.
    assert counter.calls == 1


def test_handler_does_not_cache_when_no_idempotency_key() -> None:
    cache = IdempotencyCache(ttl_seconds=60, max_entries=8)
    h = MediaIntegrityHandlers(
        service=MediaIntegrityService(policy=ServarrPolicy()),
        cache=cache,
    )
    handler = _ResponseSpy()  # no Idempotency-Key header
    h.dispatch_post(
        handler, "/api/media-integrity/reconcile", {},
        _make_actor(admin=True),
    )
    assert handler.responses[0][0] == HTTPStatus.OK
    assert cache.size() == 0


# ---------------------------------------------------------------------------
# missing_api_keys (Task 3)
# ---------------------------------------------------------------------------


def test_status_exposes_missing_keys() -> None:
    svc = MediaIntegrityService(
        policy=ServarrPolicy(), missing_keys=["sonarr"],
    )
    assert svc.status()["missing_api_keys"] == ["sonarr"]


def test_status_default_missing_keys_is_empty_list() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    assert svc.status()["missing_api_keys"] == []


# ---------------------------------------------------------------------------
# resolve_review (Task 4)
# ---------------------------------------------------------------------------


def _file(
    fid: str, *, release: str = "42", q_score: int = 5,
    added: str = "2026-04-01", size: int = 100,
) -> MediaFile:
    return MediaFile(
        id=fid, release_id=release,
        relative_path=f"{fid}.mkv", absolute_path=f"/m/{fid}.mkv",
        size=size, quality_name="WEBDL", quality_score=q_score,
        added_at=added,
    )


def test_resolve_review_servarr_deletes_losers_only() -> None:
    release = MediaRelease(id="42", title="Spider-Man", path="/m")
    a = _file("a")
    b = _file("b", size=200)
    c = _file("c", size=50)
    arr = _FakeArrAdapter(
        releases=[release], files_by_release={"42": [a, b, c]},
    )
    svc = MediaIntegrityService(
        policy=ServarrPolicy(), servarr_adapters=[arr],
    )
    result = svc.resolve_review(
        "radarr", "42", winner_file_id="b", actor="alice",
    )
    assert sorted(arr.deleted) == ["a", "c"]
    assert result["deleted_count"] == 2
    assert result["bytes_freed"] == 100 + 50


def test_resolve_review_bazarr_deletes_only_same_group() -> None:
    sub_release = SubtitleRelease(id="100", kind="movie", title="x", path="/")
    en_keep = SubtitleFile(
        release_id="100", release_kind="movie", path="/keep.en.srt",
        language="en", score=99, added_at="2026-04-01", size=10,
    )
    en_drop = SubtitleFile(
        release_id="100", release_kind="movie", path="/drop.en.srt",
        language="en", score=80, added_at="2026-04-10", size=20,
    )
    es_other = SubtitleFile(
        release_id="100", release_kind="movie", path="/x.es.srt",
        language="es", score=70, added_at="2026-04-10", size=5,
    )
    bz = _FakeBazarr(
        releases=[sub_release],
        subs_by_release={("100", "movie"): [en_keep, en_drop, es_other]},
    )
    svc = MediaIntegrityService(
        policy=ServarrPolicy(), bazarr_adapter=bz,
    )
    result = svc.resolve_review(
        "bazarr", "100",
        winner_sub_path="/keep.en.srt",
        release_kind="movie", language="en",
        actor="alice",
    )
    assert bz.deleted == [en_drop]
    assert result["deleted_count"] == 1
    assert result["bytes_freed"] == 20


def test_resolve_review_unknown_app_raises_value_error() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    with pytest.raises(ValueError):
        svc.resolve_review("nope", "42", winner_file_id="x")


def test_resolve_review_missing_winner_raises() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    with pytest.raises(ValueError):
        svc.resolve_review("radarr", "42")


def test_resolve_review_handler_unknown_app_returns_400() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    h.dispatch_post(
        handler, "/api/media-integrity/resolve-review",
        {"app": "nope", "release_id": "42", "winner_file_id": "x"},
        _make_actor(admin=True),
    )
    assert handler.responses[0][0] == HTTPStatus.BAD_REQUEST


def test_resolve_review_handler_missing_winner_returns_400() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    h.dispatch_post(
        handler, "/api/media-integrity/resolve-review",
        {"app": "radarr", "release_id": "42"},
        _make_actor(admin=True),
    )
    assert handler.responses[0][0] == HTTPStatus.BAD_REQUEST


def test_resolve_review_handler_requires_admin() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    h.dispatch_post(
        handler, "/api/media-integrity/resolve-review",
        {"app": "radarr", "release_id": "42", "winner_file_id": "x"},
        _make_actor(admin=False),
    )
    assert handler.responses[0][0] == HTTPStatus.FORBIDDEN


class _AuditSpy:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def append(self, **kwargs: Any) -> dict[str, Any]:
        self.entries.append(kwargs)
        return kwargs


def test_resolve_review_emits_resolved_event_with_operator_flag() -> None:
    release = MediaRelease(id="42", title="Spider-Man", path="/m")
    a = _file("a")
    b = _file("b", size=99)
    arr = _FakeArrAdapter(
        releases=[release], files_by_release={"42": [a, b]},
    )
    bus = EventBus()
    captured: list[Any] = []
    bus.subscribe(
        MediaIntegrityDuplicateResolved.EVENT_TYPE, captured.append,
    )
    audit = _AuditSpy()
    svc = MediaIntegrityService(
        policy=ServarrPolicy(),
        servarr_adapters=[arr],
        audit=audit,
        event_bus=bus,
    )
    svc.resolve_review("radarr", "42", winner_file_id="a", actor="alice")
    assert len(captured) == 1
    assert captured[0].winner_file_id == "a"
    assert audit.entries
    assert audit.entries[0]["detail"]["operator_resolved"] is True


# ---------------------------------------------------------------------------
# Progress (Task 5)
# ---------------------------------------------------------------------------


def test_progress_empty_when_idle() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    assert svc.get_progress() == {"in_progress": False}


def test_progress_shows_running_during_reconcile() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    gate = threading.Event()
    svc._reconciler = _BlockingReconciler(gate)

    def runner() -> None:
        svc.reconcile()

    t = threading.Thread(target=runner)
    t.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        snapshot = svc.get_progress()
        if snapshot.get("in_progress"):
            break
        time.sleep(0.005)
    snapshot = svc.get_progress()
    assert snapshot["in_progress"] is True
    assert snapshot["op"] == "reconcile"
    assert snapshot["phase"] == "running"
    gate.set()
    t.join(timeout=2.0)
    assert svc.get_progress() == {"in_progress": False}


def test_handler_progress_endpoint() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    h.dispatch_get(
        handler, "/api/media-integrity/progress",
        _make_actor(admin=False, label="observer"),
    )
    assert handler.responses[0][0] == HTTPStatus.OK
    assert handler.responses[0][1] == {"in_progress": False}


def test_handler_progress_requires_authentication() -> None:
    svc = MediaIntegrityService(policy=ServarrPolicy())
    h = MediaIntegrityHandlers(service=svc)
    handler = _ResponseSpy()
    h.dispatch_get(
        handler, "/api/media-integrity/progress",
        Actor(username="", is_admin=False),
    )
    assert handler.responses[0][0] == HTTPStatus.UNAUTHORIZED


def test_handler_matches_new_routes() -> None:
    h = MediaIntegrityHandlers()
    assert h.matches_get("/api/media-integrity/progress")
    assert h.matches_post("/api/media-integrity/resolve-review")
