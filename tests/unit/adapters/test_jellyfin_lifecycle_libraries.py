"""Tests for ``JellyfinLifecycle.probe_libraries`` and
``ensure_libraries`` — the lifecycle-method port of the legacy
``ensure_jellyfin_libraries`` job handler (ADR-0005 Phase 5b — the
10th and final wirer; closes the last string ``ensured_by: ensure-*``
snowflake in the contracts tree).

Three families of behavior:

  * Probe maps the ``GET /Library/VirtualFolders`` JSON list to the
    tri-state ProbeResult: ``ok`` when every desired library
    (Movies / TV Shows / Music / Books) is present with the correct
    ``CollectionType``; ``failed`` with the missing list as evidence
    when any is absent; ``unknown`` when the api key is missing or
    Jellyfin is unreachable.
  * Ensurer is idempotent (skip POST when probe says ok), transient-
    failure when prerequisites aren't met (no jellyfin key),
    permanent-failure on 4xx from Jellyfin, transient-failure on
    URLError / OSError (network).
  * Drift detection — a library with the right name+type but wrong
    Locations surfaces as failed-with-drifted evidence, distinct
    from missing-entirely (operator-dashboard signal).

No real HTTP — urllib is mocked. The probe + ensurer parse JSON
shapes that match what real Jellyfin instances return, so the
assertion logic is exercised against representative payloads.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.jellyfin.libraries_wiring import (
    JellyfinLibrariesWirer,
)
from media_stack.adapters.jellyfin.lifecycle import JellyfinLifecycle
from media_stack.domain.services import OrchestrationContext


_JF_KEY = "jellyfin-test-key-abcdef"


@pytest.fixture(autouse=True)
def _clear_jellyfin_api_key_env():
    """``JellyfinLifecycle.discover_api_key`` reads
    ``JELLYFIN_API_KEY`` from ``os.environ`` as a fallback when
    ``ctx.secrets`` is empty. Clear after each test so values
    injected by one test don't leak into the next."""
    import os as _os
    yield
    _os.environ.pop("JELLYFIN_API_KEY", None)


def _ctx(*, jf_key: str = _JF_KEY) -> OrchestrationContext:
    """Build an ``OrchestrationContext`` pre-populated with the
    Jellyfin api key in ``secrets``. Disable the SQLite db read so
    the ``discover_api_key`` flow is purely env/secrets-driven for
    the wirer tests."""
    secrets: dict[str, str] = {}
    if jf_key:
        secrets["JELLYFIN_API_KEY"] = jf_key
    cfg = {
        "host": "jellyfin",
        "port": 8096,
        "scheme": "http",
        "api_key_env": "JELLYFIN_API_KEY",
        "auto_discover_api_key_from_db": False,
    }
    return OrchestrationContext(
        service_id="jellyfin",
        config=cfg,
        secrets=secrets,
        now=lambda: 1700000000.0,
    )


def _http_response(body: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *_: None
    return resp


def _library_entry(
    *,
    name: str,
    collection_type: str,
    locations: list[str] | None = None,
) -> dict:
    """Build a representative ``/Library/VirtualFolders`` list entry.
    The probe asserts on (Name, CollectionType); Locations is read
    only by the drift detector."""
    return {
        "Name": name,
        "CollectionType": collection_type,
        "Locations": locations or [f"/media/{collection_type}"],
        "ItemId": "abc123",
    }


def _all_four_libraries() -> list[dict]:
    """Builds the canonical 4-library list (Movies / TV Shows /
    Music / Books) at the expected ``/media/<type>`` paths."""
    return [
        _library_entry(name="Movies",   collection_type="movies",
                       locations=["/media/movies"]),
        _library_entry(name="TV Shows", collection_type="tvshows",
                       locations=["/media/tv"]),
        _library_entry(name="Music",    collection_type="music",
                       locations=["/media/music"]),
        _library_entry(name="Books",    collection_type="books",
                       locations=["/media/books"]),
    ]


# --- Probe behavior ---------------------------------------------------


class TestProbeLibraries:

    @patch("urllib.request.urlopen")
    def test_ok_when_all_expected_libraries_present(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps(_all_four_libraries()).encode()
        mock_open.return_value = _http_response(body)

        jl = JellyfinLifecycle()
        result = jl.probe_libraries(_ctx())
        assert result.is_ok
        assert result.evidence.get("library_count") == 4
        assert result.evidence.get("expected_count") == 4

    @patch("urllib.request.urlopen")
    def test_failed_when_one_library_missing(
        self, mock_open: MagicMock,
    ) -> None:
        # Only three of four libraries present — Books is absent.
        partial = [
            entry for entry in _all_four_libraries()
            if entry["Name"] != "Books"
        ]
        body = json.dumps(partial).encode()
        mock_open.return_value = _http_response(body)

        jl = JellyfinLifecycle()
        result = jl.probe_libraries(_ctx())
        assert result.status == "failed"
        assert "Books" in result.evidence.get("missing", [])

    @patch("urllib.request.urlopen")
    def test_failed_when_multiple_libraries_missing(
        self, mock_open: MagicMock,
    ) -> None:
        # Only Movies + TV present; Music + Books absent.
        partial = [
            entry for entry in _all_four_libraries()
            if entry["Name"] in ("Movies", "TV Shows")
        ]
        body = json.dumps(partial).encode()
        mock_open.return_value = _http_response(body)

        jl = JellyfinLifecycle()
        result = jl.probe_libraries(_ctx())
        assert result.status == "failed"
        missing = result.evidence.get("missing", [])
        assert "Music" in missing and "Books" in missing

    @patch("urllib.request.urlopen")
    def test_failed_with_drifted_evidence_when_path_wrong(
        self, mock_open: MagicMock,
    ) -> None:
        # All four libraries present by name+type but Movies points
        # at a legacy /opt/jellyfin/movies path. The original
        # http_json probe in jellyfin.yaml asserted Locations
        # starts-with /media/*; the lifecycle probe preserves that
        # contract by surfacing drift as failed-with-drifted evidence.
        drifted = [
            _library_entry(
                name="Movies", collection_type="movies",
                locations=["/opt/jellyfin/movies"],
            ),
            *(
                e for e in _all_four_libraries()
                if e["Name"] != "Movies"
            ),
        ]
        body = json.dumps(drifted).encode()
        mock_open.return_value = _http_response(body)

        jl = JellyfinLifecycle()
        result = jl.probe_libraries(_ctx())
        assert result.status == "failed"
        # Drift evidence surfaces the (name, expected_path,
        # actual_locations) tuple so operator dashboards can show
        # the mismatch.
        drift_evidence = result.evidence.get("drifted") or []
        assert len(drift_evidence) == 1
        assert drift_evidence[0]["name"] == "Movies"
        assert drift_evidence[0]["expected_path"] == "/media/movies"

    @patch("urllib.request.urlopen")
    def test_unknown_when_jellyfin_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")

        jl = JellyfinLifecycle()
        result = jl.probe_libraries(_ctx())
        assert result.status == "unknown"

    def test_unknown_when_no_jellyfin_api_key(self) -> None:
        jl = JellyfinLifecycle()
        result = jl.probe_libraries(_ctx(jf_key=""))
        assert result.status == "unknown"
        assert "api key" in result.detail.lower()

    def test_unknown_when_no_host_in_config(self) -> None:
        ctx = OrchestrationContext(
            service_id="jellyfin",
            config={"port": 8096, "auto_discover_api_key_from_db": False},
            secrets={"JELLYFIN_API_KEY": _JF_KEY},
            now=lambda: 0.0,
        )
        jl = JellyfinLifecycle()
        result = jl.probe_libraries(ctx)
        assert result.status == "unknown"


# --- Ensurer behavior -------------------------------------------------


class TestEnsureLibraries:

    @patch("urllib.request.urlopen")
    def test_idempotent_when_all_libraries_present(
        self, mock_open: MagicMock,
    ) -> None:
        # GET returns the canonical 4 — ensurer short-circuits, no
        # POST fires.
        body = json.dumps(_all_four_libraries()).encode()
        mock_open.return_value = _http_response(body)

        jl = JellyfinLifecycle()
        outcome = jl.ensure_libraries(_ctx())
        assert outcome.ok
        assert outcome.evidence.get("reason") == "already_configured"
        # Exactly one urlopen call (the GET); no POST.
        assert mock_open.call_count == 1

    @patch("urllib.request.urlopen")
    def test_posts_each_missing_library(
        self, mock_open: MagicMock,
    ) -> None:
        # GET returns empty — ensurer POSTs each of the four desired
        # libraries.
        responses = [
            _http_response(b"[]"),
            _http_response(b"", status=204),
            _http_response(b"", status=204),
            _http_response(b"", status=204),
            _http_response(b"", status=204),
        ]
        mock_open.side_effect = responses

        jl = JellyfinLifecycle()
        outcome = jl.ensure_libraries(_ctx())
        assert outcome.ok
        # 1 GET + 4 POST = 5 total
        assert mock_open.call_count == 5

        # Inspect each POST: verify URL, method, header, query string.
        post_calls = mock_open.call_args_list[1:]
        names_posted = []
        for call in post_calls:
            req = call.args[0]
            assert req.get_method() == "POST"
            assert "/Library/VirtualFolders?" in req.full_url
            assert req.headers.get("X-emby-token") == _JF_KEY
            # Pull the ``name`` query param out of the URL —
            # legacy-shape verifies the URL-encoded payload.
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(req.full_url).query)
            names_posted.append(qs["name"][0])
            assert qs["refreshLibrary"][0] == "false"
            # Path mirrors the desired-library spec.
            assert qs["paths"][0].startswith("/media/")

        assert set(names_posted) == {
            "Movies", "TV Shows", "Music", "Books",
        }
        added = outcome.evidence.get("added", [])
        assert set(added) == {"Movies", "TV Shows", "Music", "Books"}

    @patch("urllib.request.urlopen")
    def test_posts_only_the_missing_subset(
        self, mock_open: MagicMock,
    ) -> None:
        # GET returns 2 of 4 libraries — ensurer POSTs only the
        # missing two.
        partial = [
            entry for entry in _all_four_libraries()
            if entry["Name"] in ("Movies", "TV Shows")
        ]
        responses = [
            _http_response(json.dumps(partial).encode()),
            _http_response(b"", status=204),
            _http_response(b"", status=204),
        ]
        mock_open.side_effect = responses

        jl = JellyfinLifecycle()
        outcome = jl.ensure_libraries(_ctx())
        assert outcome.ok
        # 1 GET + 2 POSTs = 3 calls
        assert mock_open.call_count == 3
        added = outcome.evidence.get("added", [])
        assert set(added) == {"Music", "Books"}

    def test_transient_failure_when_no_jellyfin_key(self) -> None:
        jl = JellyfinLifecycle()
        outcome = jl.ensure_libraries(_ctx(jf_key=""))
        assert not outcome.ok
        assert outcome.transient is True
        assert "api key" in outcome.error.lower()

    def test_permanent_failure_when_no_host_in_config(self) -> None:
        ctx = OrchestrationContext(
            service_id="jellyfin",
            config={"port": 8096, "auto_discover_api_key_from_db": False},
            secrets={"JELLYFIN_API_KEY": _JF_KEY},
            now=lambda: 0.0,
        )
        jl = JellyfinLifecycle()
        outcome = jl.ensure_libraries(ctx)
        assert not outcome.ok
        assert outcome.transient is False

    @patch("urllib.request.urlopen")
    def test_permanent_failure_on_jellyfin_4xx(
        self, mock_open: MagicMock,
    ) -> None:
        # GET ok-but-empty, first POST returns 400 from Jellyfin —
        # payload-level problem with the request, not transient.
        responses = [
            _http_response(b"[]"),
            urllib.error.HTTPError(
                "http://jellyfin/Library/VirtualFolders", 400,
                "Bad", {}, None,
            ),
        ]
        mock_open.side_effect = responses

        jl = JellyfinLifecycle()
        outcome = jl.ensure_libraries(_ctx())
        assert not outcome.ok
        assert outcome.transient is False
        assert outcome.evidence.get("http_status") == 400

    @patch("urllib.request.urlopen")
    def test_transient_failure_when_list_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        # Initial GET fails — ensurer can't decide if anything is
        # missing. Surface as transient (orchestrator retries).
        mock_open.side_effect = urllib.error.URLError("dns")

        jl = JellyfinLifecycle()
        outcome = jl.ensure_libraries(_ctx())
        assert not outcome.ok
        assert outcome.transient is True

    @patch("urllib.request.urlopen")
    def test_transient_failure_on_post_url_error(
        self, mock_open: MagicMock,
    ) -> None:
        # GET ok-but-empty, first POST URLError — Jellyfin is warming
        # up. Surface as transient.
        responses = [
            _http_response(b"[]"),
            urllib.error.URLError("connection refused"),
        ]
        mock_open.side_effect = responses

        jl = JellyfinLifecycle()
        outcome = jl.ensure_libraries(_ctx())
        assert not outcome.ok
        assert outcome.transient is True


# --- Constructor-injection coverage -----------------------------------


class TestLibrarySpecInjection:
    """The wirer's library spec is constructor-injected so tests
    can override the default 4-library set without touching module
    state. Production: the default spec wires Movies / TV Shows /
    Music / Books at /media/<type>; tests pin the override is
    actually consulted on probe."""

    @patch("urllib.request.urlopen")
    def test_custom_library_spec_pins_probe(
        self, mock_open: MagicMock,
    ) -> None:
        # Custom 1-library spec — the probe should be ok when only
        # that single library is present, NOT the default four.
        body = json.dumps([
            _library_entry(
                name="Photos", collection_type="homevideos",
                locations=["/media/photos"],
            ),
        ]).encode()
        mock_open.return_value = _http_response(body)

        wirer = JellyfinLibrariesWirer(
            library_specs=(
                ("Photos", "homevideos", "/media/photos"),
            ),
        )
        result = wirer.probe(_JF_KEY, _ctx())
        assert result.is_ok
        assert result.evidence.get("expected_count") == 1

    @patch("urllib.request.urlopen")
    def test_custom_timeouts_passed_through(
        self, mock_open: MagicMock,
    ) -> None:
        # Verify the constructor-injected timeouts reach urllib.
        mock_open.return_value = _http_response(
            json.dumps(_all_four_libraries()).encode(),
        )
        wirer = JellyfinLibrariesWirer(
            list_timeout_seconds=42,
            write_timeout_seconds=99,
        )
        wirer.probe(_JF_KEY, _ctx())
        # The timeout kwarg should match the injected list timeout.
        assert mock_open.call_args.kwargs.get("timeout") == 42
