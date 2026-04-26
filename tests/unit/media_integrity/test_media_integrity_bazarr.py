"""Tests for Bazarr adapter, subtitle reconciler, and Bazarr settings
enforcer.

Subtitle dupes are different from video dupes — same release can
legitimately have multiple subtitle files (different language,
forced flag, hi flag). The reconciler's job is to dedupe ONLY
within a (language, forced, hi) group.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from media_stack.core.events import (
    MediaIntegrityConfigEnforced,
    MediaIntegrityConfigEnforceFailed,
    MediaIntegrityDuplicateResolved,
    MediaIntegrityDuplicateReviewNeeded,
    MediaIntegrityReconcileFailed,
)
from media_stack.services.media_integrity.adapters import BazarrAdapter
from media_stack.services.media_integrity.adapters._servarr_base import (
    HttpResponse,
    ServarrHttpError,
)
from media_stack.services.media_integrity.bazarr_protocol import (
    BazarrApp,
    BazarrCapabilities,
    SubtitleFile,
    SubtitleRelease,
)
from media_stack.services.media_integrity.policy import (
    BazarrSection,
    ServarrPolicy,
)
from media_stack.services.media_integrity.subtitle_reconciler import (
    BazarrSettingsEnforcer,
    BazarrSubtitleReconciler,
    _deep_copy_dict,
    _get_dotted,
    _group_subtitles,
    _pick_subtitle_winner,
    _set_dotted,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sub(
    *,
    path: str,
    language: str = "en",
    forced: bool = False,
    hi: bool = False,
    score: int = 0,
    added_at: str = "",
    size: int = 0,
    release_id: str = "1",
    release_kind: str = "movie",
    provider: str = "",
) -> SubtitleFile:
    return SubtitleFile(
        release_id=release_id,
        release_kind=release_kind,
        path=path,
        language=language,
        forced=forced,
        hi=hi,
        provider=provider,
        score=score,
        added_at=added_at,
        size=size,
    )


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
        get_settings_raises: Exception | None = None,
        put_settings_raises: Exception | None = None,
        list_releases_raises: Exception | None = None,
        list_subs_raises: dict[str, Exception] | None = None,
        delete_raises: dict[str, Exception] | None = None,
        field_map: dict[str, str] | None = None,
    ) -> None:
        self._settings = dict(settings or {})
        self._releases = list(releases or [])
        self._subs = dict(subs_by_release or {})
        self._get_settings_raises = get_settings_raises
        self._put_settings_raises = put_settings_raises
        self._list_releases_raises = list_releases_raises
        self._list_subs_raises = dict(list_subs_raises or {})
        self._delete_raises = dict(delete_raises or {})
        self._field_map = dict(
            field_map
            or {
                "rename_files": "general.subfolder_custom",
                "auto_sync": "general.auto_sync_subs",
                "upgrade_allowed": "general.upgrade_subs",
                "ignore_deleted": "general.ignore_deleted_episodes",
            }
        )
        self.put_calls: list[dict[str, Any]] = []
        self.deleted: list[SubtitleFile] = []

    def get_settings(self) -> dict[str, Any]:
        if self._get_settings_raises:
            raise self._get_settings_raises
        return _deep_copy_dict(self._settings)

    def put_settings(self, cfg: dict[str, Any]) -> None:
        if self._put_settings_raises:
            raise self._put_settings_raises
        self.put_calls.append(_deep_copy_dict(cfg))
        self._settings = _deep_copy_dict(cfg)

    def settings_field_map(self) -> dict[str, str]:
        return dict(self._field_map)

    def list_subtitle_releases(self) -> list[SubtitleRelease]:
        if self._list_releases_raises:
            raise self._list_releases_raises
        return list(self._releases)

    def list_subtitles_for(
        self, release_id: str, release_kind: str
    ) -> list[SubtitleFile]:
        if release_id in self._list_subs_raises:
            raise self._list_subs_raises[release_id]
        return list(self._subs.get((release_id, release_kind), []))

    def delete_subtitle(self, subtitle: SubtitleFile) -> None:
        if subtitle.path in self._delete_raises:
            raise self._delete_raises[subtitle.path]
        self.deleted.append(subtitle)

    def subtitle_score(self, subtitle: SubtitleFile) -> int:
        return subtitle.score


class _AuditSpy:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def append(self, **kwargs: Any) -> dict[str, Any]:
        self.entries.append(kwargs)
        return kwargs


class _BusSpy:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# BazarrAdapter HTTP plumbing
# ---------------------------------------------------------------------------


class _FakeHttpClient:
    def __init__(
        self, canned: dict[tuple[str, str], tuple[int, bytes]]
    ) -> None:
        self._canned = canned
        self.calls: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
        timeout: float = 15.0,
    ) -> HttpResponse:
        self.calls.append((method, url, body, dict(headers)))
        status, out = self._canned.get((method, url), (404, b""))
        return HttpResponse(status=status, body=out)


def _settings_blob() -> bytes:
    return json.dumps(
        {
            "general": {
                "subfolder_custom": False,
                "auto_sync_subs": False,
                "upgrade_subs": False,
                "ignore_deleted_episodes": False,
            }
        }
    ).encode()


def _make_adapter() -> tuple[BazarrAdapter, _FakeHttpClient]:
    client = _FakeHttpClient(
        {("GET", "http://bazarr:6767/api/system/settings"): (200, _settings_blob())}
    )
    adapter = BazarrAdapter(
        base_url="http://bazarr:6767/",
        api_key="b-key",
        http_client=client,
    )
    return adapter, client


def test_bazarr_adapter_uses_uppercase_apikey_header() -> None:
    """Bazarr expects ``X-API-KEY``, not ``X-Api-Key``."""
    _, client = _make_adapter()
    assert client.calls[0][3]["X-API-KEY"] == "b-key"


def test_bazarr_adapter_satisfies_protocol() -> None:
    adapter, _ = _make_adapter()
    assert isinstance(adapter, BazarrApp)


def test_bazarr_settings_round_trip() -> None:
    adapter, client = _make_adapter()
    cfg = adapter.get_settings()
    assert cfg["general"]["upgrade_subs"] is False

    client._canned[("POST", "http://bazarr:6767/api/system/settings")] = (200, b"")
    adapter.put_settings(cfg)
    posts = [c for c in client.calls if c[0] == "POST"]
    assert posts


def test_bazarr_get_settings_rejects_non_object() -> None:
    adapter, client = _make_adapter()
    client._canned[("GET", "http://bazarr:6767/api/system/settings")] = (
        200,
        b"[]",
    )
    with pytest.raises(ServarrHttpError, match="expected object"):
        adapter.get_settings()


def test_bazarr_list_movie_subtitle_releases() -> None:
    adapter, client = _make_adapter()
    client._canned[("GET", "http://bazarr:6767/api/movies")] = (
        200,
        json.dumps(
            {
                "data": [
                    {"radarrId": 100, "title": "Spider-Man", "path": "/m/sm"},
                    {"id": 101, "title": "Dune", "path": "/m/dune"},  # legacy id
                    {"title": "Missing"},
                    "not-a-dict",
                ]
            }
        ).encode(),
    )
    client._canned[("GET", "http://bazarr:6767/api/series")] = (200, b'{"data": []}')
    releases = adapter.list_subtitle_releases()
    movie_releases = [r for r in releases if r.kind == "movie"]
    assert len(movie_releases) == 2
    assert movie_releases[0].id == "100"


def test_bazarr_list_episode_releases_flattens_series() -> None:
    adapter, client = _make_adapter()
    client._canned[("GET", "http://bazarr:6767/api/movies")] = (200, b'[]')
    client._canned[("GET", "http://bazarr:6767/api/series")] = (
        200,
        json.dumps(
            [
                {"sonarrSeriesId": 50, "title": "The Bear"},
                {"title": "no-id"},
            ]
        ).encode(),
    )
    client._canned[("GET", "http://bazarr:6767/api/episodes?seriesid=50")] = (
        200,
        json.dumps(
            [
                {
                    "sonarrEpisodeId": 500,
                    "season": 1,
                    "episode": 1,
                    "title": "System",
                    "path": "/m/bear/s01e01.mkv",
                },
                {"id": 501, "season": 1, "episode": 2, "title": "Hands"},
                {"season": 1, "episode": 3, "title": "no-id"},
                "not-a-dict",
            ]
        ).encode(),
    )
    releases = adapter.list_subtitle_releases()
    ep_releases = [r for r in releases if r.kind == "episode"]
    assert len(ep_releases) == 2
    assert "S01E01" in ep_releases[0].title


def test_bazarr_list_subtitles_for_movie() -> None:
    adapter, client = _make_adapter()
    client._canned[("GET", "http://bazarr:6767/api/movies/subtitles?radarrid=100")] = (
        200,
        json.dumps(
            [
                {
                    "path": "/m/sm/sm.en.srt",
                    "language": "en",
                    "forced": False,
                    "hi": False,
                    "provider": "opensubtitles",
                    "score": 95,
                    "timestamp": "2026-04-20",
                    "file_size": 50000,
                },
                {
                    "path": "/m/sm/sm.en.opensubs.srt",
                    "language": "en",
                    "score": "85",  # string, must coerce
                },
                {"language": "en"},  # missing path
                {"path": 42},  # non-string path
                "not-a-dict",
            ]
        ).encode(),
    )
    subs = adapter.list_subtitles_for("100", "movie")
    assert len(subs) == 2
    assert subs[0].score == 95
    assert subs[1].score == 85


def test_bazarr_list_subtitles_for_episode_uses_episodeid_query() -> None:
    adapter, client = _make_adapter()
    client._canned[
        ("GET", "http://bazarr:6767/api/episodes/subtitles?episodeid=501")
    ] = (200, json.dumps([{"path": "/x/501.en.srt", "language": "en"}]).encode())
    subs = adapter.list_subtitles_for("501", "episode")
    assert len(subs) == 1


def test_bazarr_list_subtitles_unknown_kind_returns_empty() -> None:
    adapter, _ = _make_adapter()
    assert adapter.list_subtitles_for("100", "audiobook") == []


def test_bazarr_delete_subtitle_sends_correct_body() -> None:
    adapter, client = _make_adapter()
    client._canned[("DELETE", "http://bazarr:6767/api/subtitles")] = (200, b"")
    sub = SubtitleFile(
        release_id="100",
        release_kind="movie",
        path="/m/sm/sm.en.srt",
        language="en",
        forced=False,
        hi=False,
    )
    adapter.delete_subtitle(sub)
    delete_call = next(c for c in client.calls if c[0] == "DELETE")
    body = json.loads(delete_call[2])
    assert body["type"] == "movie"
    assert body["id"] == 100  # numeric coercion
    assert body["subtitles_path"] == "/m/sm/sm.en.srt"
    assert body["language"] == "en"


def test_bazarr_delete_subtitle_keeps_string_id_when_non_numeric() -> None:
    adapter, client = _make_adapter()
    client._canned[("DELETE", "http://bazarr:6767/api/subtitles")] = (200, b"")
    sub = SubtitleFile(
        release_id="abc",
        release_kind="movie",
        path="/x/x.srt",
        language="en",
    )
    adapter.delete_subtitle(sub)
    body = json.loads(next(c for c in client.calls if c[0] == "DELETE")[2])
    assert body["id"] == "abc"


def test_bazarr_capabilities_probed_on_construction() -> None:
    adapter, _ = _make_adapter()
    assert "general.upgrade_subs" in adapter.capabilities.probed_setting_keys


def test_bazarr_capabilities_default_when_settings_unreachable() -> None:
    client = _FakeHttpClient(
        {("GET", "http://bazarr:6767/api/system/settings"): (503, b"")}
    )
    adapter = BazarrAdapter(
        base_url="http://bazarr:6767",
        api_key="k",
        http_client=client,
    )
    assert adapter.capabilities.probed_setting_keys == ()


def test_bazarr_subtitle_score_returns_score() -> None:
    adapter, _ = _make_adapter()
    sub = _sub(path="/x.srt", score=42)
    assert adapter.subtitle_score(sub) == 42


def test_bazarr_field_map() -> None:
    adapter, _ = _make_adapter()
    fm = adapter.settings_field_map()
    assert fm["upgrade_allowed"] == "general.upgrade_subs"
    assert fm["ignore_deleted"] == "general.ignore_deleted_episodes"


# ---------------------------------------------------------------------------
# Subtitle reconciler — winner-picking
# ---------------------------------------------------------------------------


def test_group_subtitles_keys_by_lang_forced_hi() -> None:
    a = _sub(path="/a.en.srt", language="en")
    b = _sub(path="/b.en.srt", language="en")
    c = _sub(path="/c.en.forced.srt", language="en", forced=True)
    d = _sub(path="/d.es.srt", language="es")
    groups = _group_subtitles([a, b, c, d])
    assert len(groups[("en", False, False)]) == 2  # a + b are dupes
    assert len(groups[("en", True, False)]) == 1   # c is alone (forced ≠ a/b)
    assert len(groups[("es", False, False)]) == 1  # d is alone


def test_pick_subtitle_winner_by_score() -> None:
    a = _sub(path="/a.srt", score=50)
    b = _sub(path="/b.srt", score=80)
    adapter = _FakeBazarr()
    winner, losers = _pick_subtitle_winner([a, b], adapter)
    assert winner is b


def test_pick_subtitle_winner_by_added_at_when_score_tied() -> None:
    a = _sub(path="/a.srt", score=80, added_at="2026-04-01")
    b = _sub(path="/b.srt", score=80, added_at="2026-04-10")
    adapter = _FakeBazarr()
    winner, _ = _pick_subtitle_winner([a, b], adapter)
    assert winner is a


def test_pick_subtitle_winner_total_tie_returns_none() -> None:
    a = _sub(path="/a.srt", score=80, added_at="2026-04-01", size=100)
    b = _sub(path="/b.srt", score=80, added_at="2026-04-01", size=100)
    adapter = _FakeBazarr()
    winner, _ = _pick_subtitle_winner([a, b], adapter)
    assert winner is None


def test_pick_subtitle_winner_single_returns_self() -> None:
    a = _sub(path="/a.srt")
    adapter = _FakeBazarr()
    winner, losers = _pick_subtitle_winner([a], adapter)
    assert winner is a and losers == []


def test_pick_subtitle_winner_empty() -> None:
    adapter = _FakeBazarr()
    winner, losers = _pick_subtitle_winner([], adapter)
    assert winner is None and losers == []


# ---------------------------------------------------------------------------
# Subtitle reconciler — integration
# ---------------------------------------------------------------------------


def test_reconciler_resolves_subtitle_dupe() -> None:
    release = SubtitleRelease(id="100", kind="movie", title="Spider-Man", path="/m/sm")
    keep = _sub(path="/m/sm/sm.en.srt", score=95, added_at="2026-04-01", size=10000, release_id="100")
    drop = _sub(path="/m/sm/sm.en.opensubs.srt", score=80, added_at="2026-04-10", size=8000, release_id="100")
    adapter = _FakeBazarr(
        releases=[release],
        subs_by_release={("100", "movie"): [keep, drop]},
    )
    bus = _BusSpy()
    audit = _AuditSpy()
    rec = BazarrSubtitleReconciler(audit=audit, event_bus=bus)
    report = rec.reconcile(adapter)

    assert len(report.resolved) == 1
    assert adapter.deleted == [drop]
    assert report.total_bytes_freed == 8000

    bus_resolved = [
        e for e in bus.events if isinstance(e, MediaIntegrityDuplicateResolved)
    ]
    assert len(bus_resolved) == 1
    assert bus_resolved[0].winner_file_id == "/m/sm/sm.en.srt"


def test_reconciler_skips_legitimate_variants() -> None:
    """``.en.srt`` + ``.en.forced.srt`` are NOT a dupe."""
    release = SubtitleRelease(id="100", kind="movie", title="X", path="/")
    en = _sub(path="/x.en.srt", language="en", release_id="100")
    forced = _sub(path="/x.en.forced.srt", language="en", forced=True, release_id="100")
    adapter = _FakeBazarr(
        releases=[release],
        subs_by_release={("100", "movie"): [en, forced]},
    )
    rec = BazarrSubtitleReconciler()
    report = rec.reconcile(adapter)
    assert len(report.resolved) == 0
    assert adapter.deleted == []


def test_reconciler_emits_review_when_total_tie() -> None:
    release = SubtitleRelease(id="1", kind="movie", title="X", path="/")
    a = _sub(path="/a.srt", score=80, added_at="2026-04-01", size=100, release_id="1")
    b = _sub(path="/b.srt", score=80, added_at="2026-04-01", size=100, release_id="1")
    adapter = _FakeBazarr(
        releases=[release], subs_by_release={("1", "movie"): [a, b]},
    )
    bus = _BusSpy()
    rec = BazarrSubtitleReconciler(event_bus=bus)
    report = rec.reconcile(adapter)
    assert len(report.needs_review) == 1
    assert any(
        isinstance(e, MediaIntegrityDuplicateReviewNeeded) for e in bus.events
    )


def test_reconciler_handles_list_releases_failure() -> None:
    adapter = _FakeBazarr(list_releases_raises=RuntimeError("503"))
    bus = _BusSpy()
    rec = BazarrSubtitleReconciler(event_bus=bus)
    report = rec.reconcile(adapter)
    assert report.failures
    assert any(
        isinstance(e, MediaIntegrityReconcileFailed) for e in bus.events
    )


def test_reconciler_handles_list_subs_failure_per_release() -> None:
    r1 = SubtitleRelease(id="ok", kind="movie", title="OK", path="/")
    r2 = SubtitleRelease(id="bad", kind="movie", title="Bad", path="/")
    keep = _sub(path="/ok.en.srt", score=90, release_id="ok", added_at="2026-04-01")
    drop = _sub(path="/ok.en.alt.srt", score=70, release_id="ok", added_at="2026-04-10")
    adapter = _FakeBazarr(
        releases=[r1, r2],
        subs_by_release={("ok", "movie"): [keep, drop]},
        list_subs_raises={"bad": RuntimeError("403")},
    )
    rec = BazarrSubtitleReconciler()
    report = rec.reconcile(adapter)
    assert len(report.resolved) == 1
    assert len(report.failures) == 1


def test_reconciler_marks_review_when_all_deletes_fail() -> None:
    release = SubtitleRelease(id="1", kind="movie", title="X", path="/")
    keep = _sub(path="/keep.srt", score=95, release_id="1", added_at="2026-04-01")
    drop = _sub(path="/drop.srt", score=80, release_id="1", added_at="2026-04-10")
    adapter = _FakeBazarr(
        releases=[release],
        subs_by_release={("1", "movie"): [keep, drop]},
        delete_raises={"/drop.srt": RuntimeError("403")},
    )
    rec = BazarrSubtitleReconciler()
    report = rec.reconcile(adapter)
    assert len(report.resolved) == 0
    assert len(report.needs_review) == 1


def test_reconciler_skips_release_with_only_one_sub_per_group() -> None:
    release = SubtitleRelease(id="1", kind="movie", title="X", path="/")
    en = _sub(path="/en.srt", language="en", release_id="1")
    es = _sub(path="/es.srt", language="es", release_id="1")
    adapter = _FakeBazarr(
        releases=[release],
        subs_by_release={("1", "movie"): [en, es]},
    )
    rec = BazarrSubtitleReconciler()
    report = rec.reconcile(adapter)
    assert len(report.resolved) == 0
    assert adapter.deleted == []


# ---------------------------------------------------------------------------
# Bazarr settings enforcer
# ---------------------------------------------------------------------------


def test_bazarr_enforcer_changes_drifted_settings() -> None:
    adapter = _FakeBazarr(
        settings={
            "general": {
                "subfolder_custom": False,  # drifted
                "auto_sync_subs": False,  # drifted
                "upgrade_subs": False,  # drifted
                "ignore_deleted_episodes": False,  # drifted
                "other_field": "untouched",
            }
        }
    )
    audit = _AuditSpy()
    bus = _BusSpy()
    enforcer = BazarrSettingsEnforcer(
        policy=ServarrPolicy(), audit=audit, event_bus=bus
    )
    report = enforcer.apply(adapter)
    assert len(report.changed_paths) == 4
    assert adapter.put_calls
    put = adapter.put_calls[0]
    # All four were flipped to True
    assert put["general"]["subfolder_custom"] is True
    assert put["general"]["upgrade_subs"] is True
    assert put["general"]["other_field"] == "untouched"  # preserved

    success = [e for e in bus.events if isinstance(e, MediaIntegrityConfigEnforced)]
    assert len(success) == 1
    assert "settings" in success[0].sections_applied


def test_bazarr_enforcer_no_op_when_compliant() -> None:
    adapter = _FakeBazarr(
        settings={
            "general": {
                "subfolder_custom": True,
                "auto_sync_subs": True,
                "upgrade_subs": True,
                "ignore_deleted_episodes": True,
            }
        }
    )
    enforcer = BazarrSettingsEnforcer(policy=ServarrPolicy())
    report = enforcer.apply(adapter)
    assert report.changed_paths == ()
    assert adapter.put_calls == []


def test_bazarr_enforcer_handles_get_failure() -> None:
    adapter = _FakeBazarr(get_settings_raises=RuntimeError("503"))
    bus = _BusSpy()
    enforcer = BazarrSettingsEnforcer(policy=ServarrPolicy(), event_bus=bus)
    report = enforcer.apply(adapter)
    assert report.failures
    assert any(
        isinstance(e, MediaIntegrityConfigEnforceFailed) for e in bus.events
    )


def test_bazarr_enforcer_handles_put_failure() -> None:
    adapter = _FakeBazarr(
        settings={"general": {"subfolder_custom": False}},
        put_settings_raises=RuntimeError("403"),
    )
    bus = _BusSpy()
    enforcer = BazarrSettingsEnforcer(policy=ServarrPolicy(), event_bus=bus)
    report = enforcer.apply(adapter)
    assert report.failures


def test_bazarr_enforcer_skips_unsupported_capability() -> None:
    """If capability flag says we don't support a key, drop it."""
    adapter = _FakeBazarr(
        settings={
            "general": {
                "subfolder_custom": False,
                "auto_sync_subs": False,
                "upgrade_subs": False,
                "ignore_deleted_episodes": False,
            }
        },
    )
    object.__setattr__(
        adapter,
        "capabilities",
        BazarrCapabilities(supports_upgrade=False),
    )
    enforcer = BazarrSettingsEnforcer(policy=ServarrPolicy())
    enforcer.apply(adapter)
    # upgrade_subs was NOT touched
    put = adapter.put_calls[0]
    assert put["general"]["upgrade_subs"] is False


def test_bazarr_enforcer_with_overridden_policy() -> None:
    """Operator can override a single Bazarr knob without editing
    the YAML."""
    adapter = _FakeBazarr(
        settings={"general": {"subfolder_custom": True}},
    )
    overridden = ServarrPolicy().with_overrides(
        bazarr=BazarrSection(rename_files=False),
    )
    enforcer = BazarrSettingsEnforcer(policy=overridden)
    report = enforcer.apply(adapter)
    # rename_files is now False and matches the adapter's True → drift
    assert "general.subfolder_custom" in report.changed_paths


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_get_dotted_returns_value() -> None:
    obj = {"a": {"b": {"c": 42}}}
    assert _get_dotted(obj, "a.b.c") == 42


def test_get_dotted_returns_none_for_missing_path() -> None:
    obj = {"a": {}}
    assert _get_dotted(obj, "a.b.c") is None


def test_get_dotted_returns_none_when_intermediate_not_dict() -> None:
    obj = {"a": "string"}
    assert _get_dotted(obj, "a.b") is None


def test_set_dotted_creates_intermediates() -> None:
    obj: dict[str, Any] = {}
    _set_dotted(obj, "a.b.c", 42)
    assert obj == {"a": {"b": {"c": 42}}}


def test_set_dotted_overwrites_non_dict_intermediate() -> None:
    obj = {"a": "string"}
    _set_dotted(obj, "a.b", 1)
    assert obj == {"a": {"b": 1}}


def test_deep_copy_dict_does_not_alias_lists() -> None:
    src = {"a": [1, 2], "b": {"c": [3, 4]}}
    dst = _deep_copy_dict(src)
    dst["a"].append(99)
    assert src["a"] == [1, 2]
    dst["b"]["c"].append(99)
    # Inner list IS deep-copied via the dict recursion + list() call
    assert src["b"]["c"] == [3, 4]


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


def test_bazarr_reconciler_dry_run_does_not_delete() -> None:
    release = SubtitleRelease(id="1", kind="movie", title="X", path="/")
    keep = _sub(path="/keep.srt", score=95, added_at="2026-04-01", size=1000, release_id="1")
    drop = _sub(path="/drop.srt", score=70, added_at="2026-04-10", size=500, release_id="1")
    adapter = _FakeBazarr(
        releases=[release],
        subs_by_release={("1", "movie"): [keep, drop]},
    )
    rec = BazarrSubtitleReconciler()
    report = rec.reconcile(adapter, dry_run=True)
    assert adapter.deleted == []
    assert report.dry_run is True
    assert len(report.resolved) == 1
    assert report.total_bytes_freed == 500
    res = report.resolved[0]
    assert res.winner_path == "/keep.srt"
    assert res.loser_paths == ("/drop.srt",)
    assert res.bytes_freed == 500


def test_bazarr_reconciler_dry_run_no_audit_on_deletions() -> None:
    from media_stack.core.auth.users.audit_actions import (
        MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
    )

    release = SubtitleRelease(id="1", kind="movie", title="X", path="/")
    keep = _sub(path="/keep.srt", score=95, added_at="2026-04-01", release_id="1")
    drop = _sub(path="/drop.srt", score=70, added_at="2026-04-10", release_id="1")
    adapter = _FakeBazarr(
        releases=[release],
        subs_by_release={("1", "movie"): [keep, drop]},
    )
    audit = _AuditSpy()
    bus = _BusSpy()
    rec = BazarrSubtitleReconciler(audit=audit, event_bus=bus)
    rec.reconcile(adapter, dry_run=True)
    resolved_audits = [
        e for e in audit.entries if e["action"] == MEDIA_INTEGRITY_DUPLICATE_RESOLVED
    ]
    assert resolved_audits == []
    resolved_events = [
        e for e in bus.events if isinstance(e, MediaIntegrityDuplicateResolved)
    ]
    assert resolved_events == []


def test_bazarr_reconciler_dry_run_still_surfaces_review() -> None:
    release = SubtitleRelease(id="1", kind="movie", title="X", path="/")
    a = _sub(path="/a.srt", score=80, added_at="2026-04-01", size=100, release_id="1")
    b = _sub(path="/b.srt", score=80, added_at="2026-04-01", size=100, release_id="1")
    adapter = _FakeBazarr(
        releases=[release], subs_by_release={("1", "movie"): [a, b]},
    )
    bus = _BusSpy()
    rec = BazarrSubtitleReconciler(event_bus=bus)
    report = rec.reconcile(adapter, dry_run=True)
    assert adapter.deleted == []
    assert len(report.needs_review) == 1
    assert any(
        isinstance(e, MediaIntegrityDuplicateReviewNeeded) for e in bus.events
    )


def test_bazarr_reconciler_default_run_is_not_dry_run() -> None:
    release = SubtitleRelease(id="1", kind="movie", title="X", path="/")
    keep = _sub(path="/keep.srt", score=95, added_at="2026-04-01", release_id="1")
    drop = _sub(path="/drop.srt", score=70, added_at="2026-04-10", release_id="1")
    adapter = _FakeBazarr(
        releases=[release],
        subs_by_release={("1", "movie"): [keep, drop]},
    )
    rec = BazarrSubtitleReconciler()
    report = rec.reconcile(adapter)
    assert report.dry_run is False
    assert adapter.deleted == [drop]
