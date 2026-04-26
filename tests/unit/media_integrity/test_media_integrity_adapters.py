"""Tests for the four Servarr adapters + the shared base.

Each adapter is exercised against a fake HTTP client returning
canned responses that match the API shapes verified live on the
k8s cluster on 2026-04-24. The goal is to pin the field-name
translations *and* the response parsing so we notice regressions
if a Servarr upgrade renames a field."""

from __future__ import annotations

import json
from typing import Any

import pytest

from media_stack.services.media_integrity.adapters import (
    LidarrAdapter,
    RadarrAdapter,
    ReadarrAdapter,
    ServarrHttpError,
    SonarrAdapter,
)
from media_stack.services.media_integrity.adapters._servarr_base import (
    HttpResponse,
    UrllibHttpClient,
    _ServarrBaseAdapter,
)
from media_stack.services.media_integrity.arr_protocol import (
    ArrApp,
    MediaFile,
    MediaRelease,
    QualityProfile,
)


class _FakeHttpClient:
    """Deterministic HTTP fake keyed on (method, url).

    - ``canned`` maps (method, url) → (status, body_bytes).
    - ``body_callable`` maps (method, url) → f(request_body) → (status, body).
      Used for endpoints that respond differently based on PUT body.
    - All requests are recorded in ``.calls`` for assertion.
    """

    def __init__(
        self,
        canned: dict[tuple[str, str], tuple[int, bytes]] | None = None,
        body_callable: dict[tuple[str, str], Any] | None = None,
    ) -> None:
        self._canned = canned or {}
        self._body_callable = body_callable or {}
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
        key = (method, url)
        if key in self._body_callable:
            status, out = self._body_callable[key](body)
            return HttpResponse(status=status, body=out)
        status, out = self._canned.get(key, (404, b""))
        return HttpResponse(status=status, body=out)


def _json(obj: Any) -> bytes:
    return json.dumps(obj).encode("utf-8")


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def _make_radarr(probe_body: bytes | None = None) -> tuple[RadarrAdapter, _FakeHttpClient]:
    if probe_body is None:
        probe_body = _json(
            {
                "autoUnmonitorPreviouslyDownloadedMovies": False,
                "copyUsingHardlinks": False,
                "deleteEmptyFolders": False,
                "importExtraFiles": False,
                "extraFileExtensions": "",
                "skipFreeSpaceCheckWhenImporting": True,
                "minimumFreeSpaceWhenImporting": 100,
                "createEmptyMovieFolders": True,
                "renameMovies": False,
                "autoUnmonitorDeletedMovies": False,
                "id": 1,
            }
        )
    client = _FakeHttpClient(
        {("GET", "http://radarr:7878/api/v3/config/mediamanagement"): (200, probe_body)}
    )
    adapter = RadarrAdapter(
        base_url="http://radarr:7878/",  # test trailing-slash stripping
        api_key="r-key",
        media_root="/media/movies",
        http_client=client,
    )
    return adapter, client


def test_adapter_satisfies_arr_app_protocol() -> None:
    adapter, _ = _make_radarr()
    assert isinstance(adapter, ArrApp)


def test_base_adapter_requires_name_and_api_version() -> None:
    class _BrokenAdapter(_ServarrBaseAdapter):
        pass

    with pytest.raises(TypeError, match="class attributes"):
        _BrokenAdapter(
            base_url="http://x",
            api_key="k",
            media_root="/m",
            http_client=_FakeHttpClient(),
        )


# ---------------------------------------------------------------------------
# Capability probing
# ---------------------------------------------------------------------------


def test_probe_detects_auto_unmonitor_support() -> None:
    adapter, _ = _make_radarr()
    assert adapter.capabilities.supports_auto_unmonitor_deleted is True
    assert "autoUnmonitorDeletedMovies" in adapter.capabilities.probed_field_names


def test_probe_reports_no_support_when_field_missing() -> None:
    probe_body = _json(
        {
            "autoUnmonitorPreviouslyDownloadedMovies": False,
            "copyUsingHardlinks": False,
            "id": 1,
        }
    )
    adapter, _ = _make_radarr(probe_body=probe_body)
    assert adapter.capabilities.supports_auto_unmonitor_deleted is False


def test_probe_failure_yields_conservative_defaults() -> None:
    """If the *arr is down at construction, we don't crash — we
    surface default capabilities so the enforcer can retry later."""
    client = _FakeHttpClient(
        {("GET", "http://radarr:7878/api/v3/config/mediamanagement"): (503, b"down")}
    )
    adapter = RadarrAdapter(
        base_url="http://radarr:7878",
        api_key="k",
        media_root="/media/movies",
        http_client=client,
    )
    assert adapter.capabilities.supports_hardlinks is True
    assert adapter.capabilities.probed_field_names == ()


# ---------------------------------------------------------------------------
# HTTP headers + auth
# ---------------------------------------------------------------------------


def test_request_sends_api_key_header() -> None:
    adapter, client = _make_radarr()
    assert client.calls[0][3]["X-Api-Key"] == "r-key"
    assert client.calls[0][3]["Accept"] == "application/json"


def test_put_sends_content_type_and_body() -> None:
    adapter, client = _make_radarr()
    put_body = {"copyUsingHardlinks": True, "id": 1}

    def _handle_put(body: bytes | None) -> tuple[int, bytes]:
        assert body is not None
        assert json.loads(body.decode()) == put_body
        return 202, b""

    client._body_callable[
        ("PUT", "http://radarr:7878/api/v3/config/mediamanagement")
    ] = _handle_put

    adapter.put_media_management(put_body)
    put_call = next(c for c in client.calls if c[0] == "PUT")
    assert put_call[3]["Content-Type"] == "application/json"


def test_request_raises_servarr_http_error_on_non_2xx() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/movie")] = (500, b"boom")
    with pytest.raises(ServarrHttpError) as excinfo:
        adapter.list_releases()
    assert excinfo.value.status == 500
    assert "boom" in str(excinfo.value)


def test_get_media_management_rejects_non_object_response() -> None:
    """Construction-time probe catches the error (fail-open for
    observability); an explicit caller still sees the exception."""
    adapter, client = _make_radarr()
    client._canned[
        ("GET", "http://radarr:7878/api/v3/config/mediamanagement")
    ] = (200, b"[]")
    with pytest.raises(ServarrHttpError, match="expected object"):
        adapter.get_media_management()


def test_get_naming_rejects_non_object_response() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/config/naming")] = (200, b"[]")
    with pytest.raises(ServarrHttpError, match="expected object"):
        adapter.get_naming()


# ---------------------------------------------------------------------------
# Radarr — list_releases + list_files_for + delete_file
# ---------------------------------------------------------------------------


def test_radarr_list_releases_parses_flat_list() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/movie")] = (
        200,
        _json(
            [
                {
                    "id": 42,
                    "title": "Spider-Man: No Way Home",
                    "year": 2021,
                    "path": "/media/movies/Spider-Man - No Way Home (2021)",
                    "qualityProfileId": 4,
                    "monitored": True,
                },
                {
                    "id": 43,
                    "title": "Dune",
                    "year": 2021,
                    "path": "/media/movies/Dune (2021)",
                    "qualityProfileId": 4,
                    "monitored": False,
                },
                "not-a-dict",  # tolerated, skipped
                {"title": "missing-id"},  # skipped
            ]
        ),
    )
    releases = adapter.list_releases()
    assert len(releases) == 2
    assert releases[0] == MediaRelease(
        id="42",
        title="Spider-Man: No Way Home",
        path="/media/movies/Spider-Man - No Way Home (2021)",
        year=2021,
        quality_profile_id=4,
        monitored=True,
    )
    assert releases[1].monitored is False


def test_radarr_list_releases_handles_non_list_response() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/movie")] = (200, b'{}')
    assert adapter.list_releases() == []


def test_radarr_list_files_for_parses_quality_envelope() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/moviefile?movieId=42")] = (
        200,
        _json(
            [
                {
                    "id": 100,
                    "relativePath": "Spider-Man 2160p.mkv",
                    "path": "/media/movies/Spider-Man (2021)/Spider-Man 2160p.mkv",
                    "size": 50_000_000_000,
                    "quality": {
                        "quality": {"id": 19, "name": "Bluray-2160p"},
                        "revision": {"version": 1, "real": 0},
                    },
                    "dateAdded": "2026-04-20T01:00:00Z",
                },
                {
                    "id": 101,
                    "relativePath": "Spider-Man 1080p.mkv",
                    "path": "/media/movies/Spider-Man (2021)/Spider-Man 1080p.mkv",
                    "size": 8_000_000_000,
                    "quality": {
                        "quality": {"id": 7, "name": "Bluray-1080p"},
                    },
                    "dateAdded": "2026-04-18T01:00:00Z",
                },
            ]
        ),
    )
    files = adapter.list_files_for("42")
    assert len(files) == 2
    assert files[0].quality_name == "Bluray-2160p"
    assert files[0].quality_score == 19
    assert files[1].quality_score == 7
    assert adapter.quality_score(files[0]) == 19


def test_radarr_list_files_for_handles_bad_shapes() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/moviefile?movieId=42")] = (
        200,
        _json([{"quality": "not-an-envelope"}, {"id": 1, "quality": None}, "strval"]),
    )
    files = adapter.list_files_for("42")
    # Only the {id: 1} entry survives; bad quality envelope yields ("", 0).
    assert len(files) == 1
    assert files[0].quality_name == ""
    assert files[0].quality_score == 0


def test_radarr_list_files_for_non_list() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/moviefile?movieId=42")] = (
        200,
        b'{}',
    )
    assert adapter.list_files_for("42") == []


def test_radarr_delete_file_hits_correct_endpoint() -> None:
    adapter, client = _make_radarr()
    client._canned[("DELETE", "http://radarr:7878/api/v3/moviefile/100")] = (200, b"")
    adapter.delete_file("100")
    delete_calls = [c for c in client.calls if c[0] == "DELETE"]
    assert delete_calls[0][1] == "http://radarr:7878/api/v3/moviefile/100"


def test_radarr_quality_profiles() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/qualityprofile")] = (
        200,
        _json(
            [
                {
                    "id": 4,
                    "name": "HD-1080p",
                    "cutoff": 7,  # sometimes int
                    "items": [{"quality": {"id": 7}, "allowed": True}],
                },
                {
                    "id": 5,
                    "name": "HD-2160p",
                    "cutoff": {"id": 19, "name": "Bluray-2160p"},  # sometimes dict
                    "items": [],
                },
                "not-a-dict",
            ]
        ),
    )
    profiles = adapter.quality_profiles()
    assert len(profiles) == 2
    assert profiles[0] == QualityProfile(
        id=4,
        name="HD-1080p",
        cutoff_id=7,
        items=({"quality": {"id": 7}, "allowed": True},),
    )
    assert profiles[1].cutoff_id == 19


def test_quality_profiles_handles_non_list() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/qualityprofile")] = (200, b"{}")
    assert adapter.quality_profiles() == []


def test_get_naming_round_trip() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/config/naming")] = (
        200,
        _json({"renameMovies": False, "id": 1}),
    )
    cfg = adapter.get_naming()
    assert cfg["renameMovies"] is False

    def _handle_put(body: bytes | None) -> tuple[int, bytes]:
        assert body is not None and b"renameMovies" in body
        return 200, b""

    client._body_callable[
        ("PUT", "http://radarr:7878/api/v3/config/naming")
    ] = _handle_put
    adapter.put_naming({"renameMovies": True, "id": 1})


# ---------------------------------------------------------------------------
# Sonarr — series→episode flatten
# ---------------------------------------------------------------------------


def _make_sonarr() -> tuple[SonarrAdapter, _FakeHttpClient]:
    client = _FakeHttpClient(
        {
            ("GET", "http://sonarr:8989/api/v3/config/mediamanagement"): (
                200,
                _json(
                    {
                        "autoUnmonitorPreviouslyDownloadedEpisodes": False,
                        "copyUsingHardlinks": False,
                        "autoUnmonitorDeletedEpisodes": False,
                        "createEmptySeriesFolders": False,
                        "id": 1,
                    }
                ),
            )
        }
    )
    adapter = SonarrAdapter(
        base_url="http://sonarr:8989",
        api_key="s-key",
        media_root="/media/tv",
        http_client=client,
    )
    return adapter, client


def test_sonarr_flattens_series_to_episode_releases() -> None:
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/series")] = (
        200,
        _json(
            [
                {
                    "id": 10,
                    "title": "The Bear",
                    "year": 2022,
                    "path": "/media/tv/The Bear",
                    "qualityProfileId": 4,
                }
            ]
        ),
    )
    client._canned[("GET", "http://sonarr:8989/api/v3/episode?seriesId=10")] = (
        200,
        _json(
            [
                {
                    "id": 1001,
                    "seriesId": 10,
                    "seasonNumber": 1,
                    "episodeNumber": 1,
                    "title": "System",
                    "monitored": True,
                },
                {
                    "id": 1002,
                    "seriesId": 10,
                    "seasonNumber": 1,
                    "episodeNumber": 2,
                    "title": "Hands",
                    "monitored": True,
                },
                "not-a-dict",
                {"seasonNumber": 1, "episodeNumber": 3, "title": "missing-id"},
            ]
        ),
    )
    releases = adapter.list_releases()
    assert len(releases) == 2
    assert releases[0].id == "1001"
    assert releases[0].title == "The Bear S01E01 — System"
    assert releases[1].id == "1002"


def test_sonarr_handles_series_without_episodes() -> None:
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/series")] = (
        200,
        _json(
            [
                {"id": 10, "title": "Show", "path": "/media/tv/Show"},
                {"title": "missing-id"},
                "not-a-dict",
            ]
        ),
    )
    client._canned[("GET", "http://sonarr:8989/api/v3/episode?seriesId=10")] = (
        200,
        b'{"not": "a list"}',
    )
    assert adapter.list_releases() == []


def test_sonarr_list_releases_non_list_series() -> None:
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/series")] = (200, b'{}')
    assert adapter.list_releases() == []


def test_sonarr_list_files_for_filters_to_target_episode() -> None:
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/episode/1001")] = (
        200,
        _json({"id": 1001, "seriesId": 10}),
    )
    client._canned[("GET", "http://sonarr:8989/api/v3/episodefile?seriesId=10")] = (
        200,
        _json(
            [
                # Linked via new ``episodes`` list
                {
                    "id": 5001,
                    "episodes": [{"id": 1001}, {"id": 1002}],  # double-episode file
                    "relativePath": "s01e01-e02.mkv",
                    "path": "/media/tv/The Bear/s01e01-e02.mkv",
                    "size": 1,
                    "quality": {"quality": {"id": 5, "name": "WEBDL-1080p"}},
                    "dateAdded": "2026-04-20",
                },
                # Linked via older singular ``episodeId``
                {
                    "id": 5002,
                    "episodeId": 1001,
                    "relativePath": "s01e01-alt.mkv",
                    "path": "/media/tv/The Bear/s01e01-alt.mkv",
                    "size": 1,
                    "quality": {"quality": {"id": 3, "name": "HDTV-1080p"}},
                    "dateAdded": "2026-04-21",
                },
                # Linked via older plural ``episodeIds``
                {
                    "id": 5003,
                    "episodeIds": [999],  # does NOT include 1001
                    "relativePath": "other.mkv",
                    "path": "/other.mkv",
                    "size": 1,
                    "quality": {"quality": {"id": 1, "name": "HDTV-720p"}},
                    "dateAdded": "2026-04-22",
                },
                "not-a-dict",
            ]
        ),
    )
    files = adapter.list_files_for("1001")
    assert {f.id for f in files} == {"5001", "5002"}


def test_sonarr_list_files_for_missing_episode_returns_empty() -> None:
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/episode/404")] = (200, b'[]')
    assert adapter.list_files_for("404") == []


def test_sonarr_list_files_for_non_list_episodefile() -> None:
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/episode/1001")] = (
        200,
        _json({"id": 1001, "seriesId": 10}),
    )
    client._canned[("GET", "http://sonarr:8989/api/v3/episodefile?seriesId=10")] = (
        200,
        b'{}',
    )
    assert adapter.list_files_for("1001") == []


def test_sonarr_list_files_for_episode_without_series_id() -> None:
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/episode/1001")] = (
        200,
        _json({"id": 1001}),
    )
    assert adapter.list_files_for("1001") == []


def test_sonarr_release_from_raw_unused_but_protocol_complete() -> None:
    adapter, _ = _make_sonarr()
    assert adapter._release_from_raw({"id": 1}) is None


# ---------------------------------------------------------------------------
# Lidarr — album + trackfile
# ---------------------------------------------------------------------------


def _make_lidarr() -> tuple[LidarrAdapter, _FakeHttpClient]:
    client = _FakeHttpClient(
        {
            ("GET", "http://lidarr:8686/api/v1/config/mediamanagement"): (
                200,
                _json(
                    {
                        "autoUnmonitorPreviouslyDownloadedTracks": False,
                        "copyUsingHardlinks": False,
                        "autoUnmonitorDeletedTracks": False,
                        "createEmptyArtistFolders": False,
                        "id": 1,
                    }
                ),
            )
        }
    )
    adapter = LidarrAdapter(
        base_url="http://lidarr:8686",
        api_key="l-key",
        media_root="/media/music",
        http_client=client,
    )
    return adapter, client


def test_lidarr_api_version_is_v1() -> None:
    adapter, _ = _make_lidarr()
    assert adapter.api_version == "v1"


def test_lidarr_list_releases_embeds_artist_and_year() -> None:
    adapter, client = _make_lidarr()
    client._canned[("GET", "http://lidarr:8686/api/v1/album")] = (
        200,
        _json(
            [
                {
                    "id": 200,
                    "title": "Continuum",
                    "releaseDate": "2009-01-27T00:00:00Z",
                    "monitored": True,
                    "artist": {
                        "artistName": "John Mayer",
                        "path": "/media/music/John Mayer",
                        "qualityProfileId": 3,
                    },
                },
                {
                    "id": 201,
                    "title": "Debut",
                    "releaseDate": "not-a-date",
                    "monitored": False,
                    "artist": None,
                },
                {"title": "missing-id"},
                "not-a-dict",
            ]
        ),
    )
    releases = adapter.list_releases()
    assert len(releases) == 2
    assert releases[0].title == "John Mayer — Continuum"
    assert releases[0].year == 2009
    assert releases[0].quality_profile_id == 3
    assert releases[1].year is None
    assert releases[1].quality_profile_id is None
    assert releases[1].title == "Debut"


def test_lidarr_list_releases_non_list() -> None:
    adapter, client = _make_lidarr()
    client._canned[("GET", "http://lidarr:8686/api/v1/album")] = (200, b"{}")
    assert adapter.list_releases() == []


def test_lidarr_list_files_for_uses_albumId_query() -> None:
    adapter, client = _make_lidarr()
    client._canned[("GET", "http://lidarr:8686/api/v1/trackfile?albumId=200")] = (
        200,
        _json(
            [
                {
                    "id": 700,
                    "relativePath": "01 Waiting on the World.flac",
                    "path": "/media/music/John Mayer/Continuum/01 Waiting on the World.flac",
                    "size": 30_000_000,
                    "quality": {"quality": {"id": 4, "name": "FLAC"}},
                    "dateAdded": "2026-04-20",
                }
            ]
        ),
    )
    files = adapter.list_files_for("200")
    assert len(files) == 1
    assert files[0].quality_name == "FLAC"


def test_lidarr_list_files_for_non_list() -> None:
    adapter, client = _make_lidarr()
    client._canned[("GET", "http://lidarr:8686/api/v1/trackfile?albumId=200")] = (
        200,
        b"{}",
    )
    assert adapter.list_files_for("200") == []


def test_lidarr_field_map_uses_tracks_suffix() -> None:
    adapter, _ = _make_lidarr()
    fm = adapter.media_management_field_map()
    assert fm["auto_unmonitor_previously_downloaded"] == "autoUnmonitorPreviouslyDownloadedTracks"
    assert fm["unmonitor_deleted"] == "autoUnmonitorDeletedTracks"
    assert fm["create_empty_media_folders"] == "createEmptyArtistFolders"
    assert adapter.naming_field_map() == {"rename_files": "renameTracks"}


# ---------------------------------------------------------------------------
# Readarr — book + bookfile
# ---------------------------------------------------------------------------


def _make_readarr() -> tuple[ReadarrAdapter, _FakeHttpClient]:
    client = _FakeHttpClient(
        {
            ("GET", "http://readarr:8787/api/v1/config/mediamanagement"): (
                200,
                _json(
                    {
                        "autoUnmonitorPreviouslyDownloadedBooks": False,
                        "copyUsingHardlinks": False,
                        "autoUnmonitorDeletedBooks": False,
                        "createEmptyAuthorFolders": False,
                        "id": 1,
                    }
                ),
            )
        }
    )
    adapter = ReadarrAdapter(
        base_url="http://readarr:8787",
        api_key="rdr-key",
        media_root="/media/books",
        http_client=client,
    )
    return adapter, client


def test_readarr_list_releases_embeds_author_and_year() -> None:
    adapter, client = _make_readarr()
    client._canned[("GET", "http://readarr:8787/api/v1/book")] = (
        200,
        _json(
            [
                {
                    "id": 300,
                    "title": "Project Hail Mary",
                    "releaseDate": "2021-05-04T00:00:00Z",
                    "monitored": True,
                    "author": {
                        "authorName": "Andy Weir",
                        "path": "/media/books/Andy Weir",
                        "qualityProfileId": 2,
                    },
                },
                {
                    "id": 301,
                    "title": "Untitled",
                    "releaseDate": None,
                    "monitored": False,
                    "author": None,
                },
            ]
        ),
    )
    releases = adapter.list_releases()
    assert len(releases) == 2
    assert releases[0].title == "Andy Weir — Project Hail Mary"
    assert releases[0].year == 2021
    assert releases[0].quality_profile_id == 2
    assert releases[1].year is None


def test_readarr_list_releases_non_list() -> None:
    adapter, client = _make_readarr()
    client._canned[("GET", "http://readarr:8787/api/v1/book")] = (200, b"{}")
    assert adapter.list_releases() == []


def test_readarr_list_files_for() -> None:
    adapter, client = _make_readarr()
    client._canned[("GET", "http://readarr:8787/api/v1/bookfile?bookId=300")] = (
        200,
        _json(
            [
                {
                    "id": 800,
                    "relativePath": "Project Hail Mary.epub",
                    "path": "/media/books/Andy Weir/Project Hail Mary.epub",
                    "size": 2_500_000,
                    "quality": {"quality": {"id": 2, "name": "EPUB"}},
                    "dateAdded": "2026-04-20",
                }
            ]
        ),
    )
    files = adapter.list_files_for("300")
    assert files[0].quality_name == "EPUB"


def test_readarr_list_files_for_non_list() -> None:
    adapter, client = _make_readarr()
    client._canned[("GET", "http://readarr:8787/api/v1/bookfile?bookId=300")] = (
        200,
        b"{}",
    )
    assert adapter.list_files_for("300") == []


def test_readarr_field_map_uses_books_suffix() -> None:
    adapter, _ = _make_readarr()
    fm = adapter.media_management_field_map()
    assert fm["auto_unmonitor_previously_downloaded"] == "autoUnmonitorPreviouslyDownloadedBooks"
    assert fm["unmonitor_deleted"] == "autoUnmonitorDeletedBooks"
    assert fm["create_empty_media_folders"] == "createEmptyAuthorFolders"
    assert adapter.naming_field_map() == {"rename_files": "renameBooks"}


# ---------------------------------------------------------------------------
# Empty-body handling
# ---------------------------------------------------------------------------


def test_empty_response_body_yields_empty_dict() -> None:
    adapter, client = _make_radarr()
    client._canned[("DELETE", "http://radarr:7878/api/v3/moviefile/100")] = (204, b"")
    adapter.delete_file("100")


def test_request_json_empty_body_returns_empty_dict() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/config/naming")] = (200, b"")
    assert adapter.get_naming() == {}


# ---------------------------------------------------------------------------
# UrllibHttpClient — smoke test against a local socket, no network
# ---------------------------------------------------------------------------


def test_urllib_http_client_handles_http_error(monkeypatch) -> None:
    """``UrllibHttpClient`` catches ``HTTPError`` and returns a body
    so callers can handle it uniformly."""
    import io
    from urllib import error as urlerror

    class _Req:
        def __init__(self, url: str, **_: Any) -> None:
            self.url = url

    def _fake_urlopen(req, timeout):
        raise urlerror.HTTPError(
            req.url if hasattr(req, "url") else "", 500, "err", {}, io.BytesIO(b"oops")
        )

    client = UrllibHttpClient()
    monkeypatch.setattr(
        "media_stack.services.media_integrity.adapters._servarr_base.urllib.request.urlopen",
        _fake_urlopen,
    )
    resp = client.request("GET", "http://x/y", headers={})
    assert resp.status == 500
    assert resp.body == b"oops"


# ---------------------------------------------------------------------------
# list_releases_for_file — Task 3 protocol method
# ---------------------------------------------------------------------------


def test_radarr_list_releases_for_file_returns_single_id() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/moviefile/100")] = (
        200,
        _json({"id": 100, "movieId": 42, "relativePath": "x.mkv"}),
    )
    assert adapter.list_releases_for_file("100") == ["42"]


def test_radarr_list_releases_for_file_returns_empty_on_http_error() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/moviefile/404")] = (404, b"")
    assert adapter.list_releases_for_file("404") == []


def test_radarr_list_releases_for_file_returns_empty_when_parent_missing() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/moviefile/100")] = (
        200,
        _json({"id": 100}),  # no movieId
    )
    assert adapter.list_releases_for_file("100") == []


def test_radarr_list_releases_for_file_returns_empty_on_non_object() -> None:
    adapter, client = _make_radarr()
    client._canned[("GET", "http://radarr:7878/api/v3/moviefile/100")] = (
        200,
        _json([1, 2, 3]),
    )
    assert adapter.list_releases_for_file("100") == []


def test_lidarr_list_releases_for_file_returns_single_id() -> None:
    adapter, client = _make_lidarr()
    client._canned[("GET", "http://lidarr:8686/api/v1/trackfile/700")] = (
        200,
        _json({"id": 700, "albumId": 200}),
    )
    assert adapter.list_releases_for_file("700") == ["200"]


def test_readarr_list_releases_for_file_returns_single_id() -> None:
    adapter, client = _make_readarr()
    client._canned[("GET", "http://readarr:8787/api/v1/bookfile/800")] = (
        200,
        _json({"id": 800, "bookId": 300}),
    )
    assert adapter.list_releases_for_file("800") == ["300"]


def test_sonarr_list_releases_for_file_returns_all_linked_episodes() -> None:
    """A double-episode file linked to eps 1001 + 1002 returns both."""
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/episodefile/5001")] = (
        200,
        _json(
            {
                "id": 5001,
                "episodes": [{"id": 1001}, {"id": 1002}],
                "relativePath": "s01e01-e02.mkv",
            }
        ),
    )
    linked = adapter.list_releases_for_file("5001")
    assert sorted(linked) == ["1001", "1002"]


def test_sonarr_list_releases_for_file_handles_singular_episodeId() -> None:
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/episodefile/5002")] = (
        200,
        _json({"id": 5002, "episodeId": 1001}),
    )
    assert adapter.list_releases_for_file("5002") == ["1001"]


def test_sonarr_list_releases_for_file_empty_on_http_error() -> None:
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/episodefile/9999")] = (404, b"")
    assert adapter.list_releases_for_file("9999") == []


def test_sonarr_list_releases_for_file_empty_on_non_object() -> None:
    adapter, client = _make_sonarr()
    client._canned[("GET", "http://sonarr:8989/api/v3/episodefile/5001")] = (
        200,
        b'[]',
    )
    assert adapter.list_releases_for_file("5001") == []


def test_urllib_http_client_happy_path(monkeypatch) -> None:
    class _FakeResp:
        status = 200

        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *a: Any) -> None:
            pass

    def _fake_urlopen(req, timeout):
        return _FakeResp()

    monkeypatch.setattr(
        "media_stack.services.media_integrity.adapters._servarr_base.urllib.request.urlopen",
        _fake_urlopen,
    )
    client = UrllibHttpClient()
    resp = client.request("GET", "http://x/y", headers={}, body=b"payload")
    assert resp.status == 200
    assert resp.body == b'{"ok": true}'


def test_urllib_http_client_follows_307_redirect(monkeypatch) -> None:
    """Servarr instances with URL-base routing reply 307 with a
    Location header — the client must follow."""
    import io
    from urllib import error as urlerror

    class _Headers(dict):
        pass

    call_count = {"n": 0}

    class _FakeResp:
        status = 200

        def read(self) -> bytes:
            return b'{"final": true}'

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *a: Any) -> None:
            pass

    def _fake_urlopen(req, timeout):
        call_count["n"] += 1
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if call_count["n"] == 1:
            assert url == "http://radarr:7878/api/v3/config/mediamanagement"
            headers = _Headers({"Location": "/app/radarr/api/v3/config/mediamanagement"})
            raise urlerror.HTTPError(url, 307, "redirect", headers, io.BytesIO(b""))
        assert url == "http://radarr:7878/app/radarr/api/v3/config/mediamanagement"
        return _FakeResp()

    monkeypatch.setattr(
        "media_stack.services.media_integrity.adapters._servarr_base.urllib.request.urlopen",
        _fake_urlopen,
    )
    client = UrllibHttpClient()
    resp = client.request(
        "GET",
        "http://radarr:7878/api/v3/config/mediamanagement",
        headers={"X-Api-Key": "k"},
    )
    assert resp.status == 200
    assert resp.body == b'{"final": true}'
    assert call_count["n"] == 2


def test_urllib_http_client_refuses_cross_host_redirect(monkeypatch) -> None:
    """SSRF guard — a redirect to a different host must NOT be followed."""
    import io
    from urllib import error as urlerror

    class _Headers(dict):
        pass

    call_count = {"n": 0}

    def _fake_urlopen(req, timeout):
        call_count["n"] += 1
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if call_count["n"] == 1:
            headers = _Headers({"Location": "http://attacker:80/exfiltrate"})
            raise urlerror.HTTPError(url, 302, "redirect", headers, io.BytesIO(b"nope"))
        raise AssertionError("should not have followed cross-host redirect")

    monkeypatch.setattr(
        "media_stack.services.media_integrity.adapters._servarr_base.urllib.request.urlopen",
        _fake_urlopen,
    )
    client = UrllibHttpClient()
    resp = client.request("GET", "http://radarr:7878/api/v3/movie", headers={})
    assert resp.status == 302  # surfaced, not followed
    assert call_count["n"] == 1


def test_urllib_http_client_caps_redirect_hops(monkeypatch) -> None:
    """Infinite-redirect loop must not hang the controller."""
    import io
    from urllib import error as urlerror

    call_count = {"n": 0}

    def _fake_urlopen(req, timeout):
        call_count["n"] += 1
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        headers = {"Location": url + "/"}
        raise urlerror.HTTPError(url, 307, "loop", headers, io.BytesIO(b"loop"))

    monkeypatch.setattr(
        "media_stack.services.media_integrity.adapters._servarr_base.urllib.request.urlopen",
        _fake_urlopen,
    )
    client = UrllibHttpClient()
    resp = client.request("GET", "http://x/y", headers={})
    assert resp.status == 307  # last status surfaced
    assert call_count["n"] == 4  # initial + 3 redirects


def test_urllib_http_client_307_preserves_method_and_body(monkeypatch) -> None:
    """RFC 7231: 307 preserves method + body. A PUT must remain a PUT
    after the redirect — critical for ``enforce_config`` writes."""
    import io
    from urllib import error as urlerror

    methods_seen: list[str] = []
    bodies_seen: list[bytes | None] = []

    class _FakeResp:
        status = 200

        def read(self) -> bytes:
            return b""

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *a: Any) -> None:
            pass

    def _fake_urlopen(req, timeout):
        methods_seen.append(req.get_method())
        bodies_seen.append(req.data)
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if len(methods_seen) == 1:
            headers = {"Location": "/app/radarr/api/v3/config/mediamanagement"}
            raise urlerror.HTTPError(url, 307, "redirect", headers, io.BytesIO(b""))
        return _FakeResp()

    monkeypatch.setattr(
        "media_stack.services.media_integrity.adapters._servarr_base.urllib.request.urlopen",
        _fake_urlopen,
    )
    client = UrllibHttpClient()
    client.request(
        "PUT",
        "http://radarr:7878/api/v3/config/mediamanagement",
        headers={"X-Api-Key": "k"},
        body=b'{"copyUsingHardlinks": true}',
    )
    assert methods_seen == ["PUT", "PUT"]
    assert bodies_seen[1] == b'{"copyUsingHardlinks": true}'
