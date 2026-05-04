"""Tests for ``QbittorrentLifecycle.probe_categories`` and
``ensure_categories`` — the lifecycle-method port of the legacy
``ensure_qbittorrent_categories`` job handler (ADR-0005 Phase 3
cutover).

Three families of behavior:

  * Probe maps the qBit category-listing JSON to the tri-state
    ``ProbeResult``: ``ok`` (every desired category present),
    ``failed`` (login OK, listing OK, at least one missing —
    orchestrator dispatches the ensurer), ``unknown`` (cannot
    reach qBit, login failed, credential missing — orchestrator
    retries on next tick).
  * Ensurer is idempotent (skips the per-category POST when the
    category already appears in the listing) and treats a 409
    Conflict on createCategory as "already there" rather than as
    a failure (race with concurrent operator action).
  * Missing-credentials returns ``transient=True`` so the
    orchestrator retries after the operator sets
    ``QBIT_PASSWORD`` (or its secret-mapped equivalent). This is
    the silent-error-as-ok bug class the legacy handler shipped:
    a missing credential logged ``err=login failed: …`` while
    returning ``status=ok``.

No real HTTP — ``urllib.request.build_opener`` is mocked. The
cookie-jar shape doesn't need exercising directly because the
opener mock returns canned responses regardless of jar state;
the wirer's contract is "single per-call opener with cookie
support" not "track session cookies through tests".
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.qbittorrent.categories_wiring import (
    CategoriesWirer,
)
from media_stack.adapters.qbittorrent.lifecycle import QbittorrentLifecycle
from media_stack.domain.services import OrchestrationContext


_DESIRED_NAMES = ("movies", "tv", "music", "books")


# --- fixtures --------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_credential_envs(monkeypatch):
    """Each test starts with a clean credential environment so
    leakage from a prior test (or the real shell) can't mask a
    missing-credentials assertion."""
    for var in ("QBIT_USERNAME", "QBIT_PASSWORD", "QBITTORRENT_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    yield


def _ctx(
    *,
    host: str | None = "qbittorrent",
    port: int | None = 8080,
    username: str | None = None,
    password: str | None = "adminadmin",
) -> OrchestrationContext:
    """Build an ``OrchestrationContext`` pre-populated with the
    qBit host/port + (optional) credentials in ``secrets``. Mirrors
    the contract YAML's ``service:`` shape."""
    cfg: dict[str, Any] = {"scheme": "http"}
    if host:
        cfg["host"] = host
    if port is not None:
        cfg["port"] = port
    secrets: dict[str, str] = {}
    if username:
        secrets["QBIT_USERNAME"] = username
    if password:
        secrets["QBIT_PASSWORD"] = password
    return OrchestrationContext(
        service_id="qbittorrent",
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


def _categories_json(*names: str) -> bytes:
    return json.dumps(
        {n: {"name": n, "savePath": f"/data/torrents/completed/{n}"}
         for n in names}
    ).encode()


def _login_ok_response() -> MagicMock:
    return _http_response(b"Ok.")


def _build_opener_returning(*responses) -> MagicMock:
    """Build a ``build_opener`` mock whose ``open`` cycles through
    the supplied iterable of responses (or raises if the entry is
    an exception class instance)."""
    opener = MagicMock()
    iterator = iter(responses)

    def _open_side_effect(*_args, **_kwargs):
        try:
            entry = next(iterator)
        except StopIteration as exc:
            raise AssertionError(
                "opener.open called more times than responses queued",
            ) from exc
        if isinstance(entry, BaseException):
            raise entry
        return entry

    opener.open.side_effect = _open_side_effect
    return opener


# --- Probe behavior --------------------------------------------------


class TestProbeCategoriesTriState:
    """Probe distinguishes ok / failed / unknown per the
    ``ProbeResult`` contract. Ratchet against silent-error-as-ok
    regression — the legacy handler returned ``status=ok`` on login
    failure; the lifecycle path MUST NOT do that."""

    @patch("urllib.request.build_opener")
    def test_ok_when_all_desired_present(self, mock_build) -> None:
        opener = _build_opener_returning(
            _login_ok_response(),
            _http_response(_categories_json(*_DESIRED_NAMES)),
        )
        mock_build.return_value = opener

        result = QbittorrentLifecycle().probe_categories(_ctx())
        assert result.is_ok
        assert set(result.evidence["present"]) >= set(_DESIRED_NAMES)

    @patch("urllib.request.build_opener")
    def test_failed_when_some_desired_missing(self, mock_build) -> None:
        opener = _build_opener_returning(
            _login_ok_response(),
            _http_response(_categories_json("movies", "tv")),
        )
        mock_build.return_value = opener

        result = QbittorrentLifecycle().probe_categories(_ctx())
        assert result.status == "failed"
        assert set(result.evidence["missing"]) == {"music", "books"}

    @patch("urllib.request.build_opener")
    def test_unknown_when_qbit_unreachable(self, mock_build) -> None:
        opener = _build_opener_returning(
            urllib.error.URLError("Name or service not known"),
        )
        mock_build.return_value = opener

        result = QbittorrentLifecycle().probe_categories(_ctx())
        assert result.status == "unknown"
        assert "login failed" in result.detail

    @patch("urllib.request.build_opener")
    def test_unknown_when_login_returns_fails(self, mock_build) -> None:
        # qBit returns 200 + ``Fails.`` body on bad creds; the
        # wirer must not treat that as success.
        opener = _build_opener_returning(_http_response(b"Fails."))
        mock_build.return_value = opener

        result = QbittorrentLifecycle().probe_categories(_ctx())
        assert result.status == "unknown"

    def test_unknown_when_no_password(self) -> None:
        # Patch the lifecycle's wirer to one with no factory-default
        # credentials so the "no credential anywhere" path is
        # exercised (production always has the qBit factory default
        # ``admin``/``adminadmin`` as a final fallback).
        from media_stack.adapters.qbittorrent import lifecycle as lc_mod
        no_default_wirer = CategoriesWirer(
            default_username="", default_password="",
        )
        with patch.object(lc_mod, "_CATEGORIES_WIRER", no_default_wirer):
            result = QbittorrentLifecycle().probe_categories(
                _ctx(password=None),
            )
        assert result.status == "unknown"
        assert "QBIT_PASSWORD" in result.detail

    def test_unknown_when_no_host(self) -> None:
        result = QbittorrentLifecycle().probe_categories(
            _ctx(host=None),
        )
        assert result.status == "unknown"
        assert "host" in result.detail.lower()

    @patch("urllib.request.build_opener")
    def test_unknown_when_listing_returns_non_dict(self, mock_build) -> None:
        # qBit's category-list endpoint is documented to return a
        # JSON object. A list / scalar / non-JSON body counts as a
        # listing failure, which is unknown — orchestrator can't
        # decide whether ensurer would help.
        opener = _build_opener_returning(
            _login_ok_response(),
            _http_response(b"not-json{"),
        )
        mock_build.return_value = opener

        result = QbittorrentLifecycle().probe_categories(_ctx())
        assert result.status == "unknown"


# --- Ensurer behavior ------------------------------------------------


class TestEnsureCategoriesIdempotent:
    """The ensurer iterates desired categories, skipping those that
    appear in the listing. ``Outcome.success`` when every category is
    now present; per-category failures fold into a single
    ``Outcome.failure`` with the partial-progress evidence."""

    @patch("urllib.request.build_opener")
    def test_success_when_all_already_present(self, mock_build) -> None:
        opener = _build_opener_returning(
            _login_ok_response(),
            _http_response(_categories_json(*_DESIRED_NAMES)),
        )
        mock_build.return_value = opener

        outcome = QbittorrentLifecycle().ensure_categories(_ctx())
        assert outcome.ok
        assert outcome.evidence["created"] == []
        assert sorted(outcome.evidence["skipped"]) == sorted(_DESIRED_NAMES)

    @patch("urllib.request.build_opener")
    def test_creates_only_missing(self, mock_build) -> None:
        # Two pre-existing, two missing — exactly two POSTs follow
        # the listing.
        opener = _build_opener_returning(
            _login_ok_response(),
            _http_response(_categories_json("movies", "tv")),
            _http_response(b"", status=200),  # createCategory("music")
            _http_response(b"", status=200),  # createCategory("books")
        )
        mock_build.return_value = opener

        outcome = QbittorrentLifecycle().ensure_categories(_ctx())
        assert outcome.ok
        assert sorted(outcome.evidence["created"]) == ["books", "music"]
        assert sorted(outcome.evidence["skipped"]) == ["movies", "tv"]
        # Login + list + 2 POSTs = 4 calls
        assert opener.open.call_count == 4

    @patch("urllib.request.build_opener")
    def test_409_on_create_treated_as_already_there(
        self, mock_build,
    ) -> None:
        # Race condition: probe missed the category but a concurrent
        # operator action created it. 409 from createCategory =>
        # treat as success for that category.
        opener = _build_opener_returning(
            _login_ok_response(),
            _http_response(_categories_json()),
            urllib.error.HTTPError(
                "u", 409, "Conflict", {}, None,
            ),
            urllib.error.HTTPError(
                "u", 409, "Conflict", {}, None,
            ),
            urllib.error.HTTPError(
                "u", 409, "Conflict", {}, None,
            ),
            urllib.error.HTTPError(
                "u", 409, "Conflict", {}, None,
            ),
        )
        mock_build.return_value = opener

        outcome = QbittorrentLifecycle().ensure_categories(_ctx())
        assert outcome.ok

    @patch("urllib.request.build_opener")
    def test_permanent_failure_on_non_409_4xx(self, mock_build) -> None:
        opener = _build_opener_returning(
            _login_ok_response(),
            _http_response(_categories_json()),
            urllib.error.HTTPError("u", 400, "Bad Request", {}, None),
        )
        mock_build.return_value = opener

        outcome = QbittorrentLifecycle().ensure_categories(_ctx())
        assert not outcome.ok
        assert outcome.transient is False
        assert outcome.evidence["http_status"] == 400

    @patch("urllib.request.build_opener")
    def test_transient_failure_on_network_error(self, mock_build) -> None:
        opener = _build_opener_returning(
            _login_ok_response(),
            _http_response(_categories_json()),
            urllib.error.URLError("connection reset"),
        )
        mock_build.return_value = opener

        outcome = QbittorrentLifecycle().ensure_categories(_ctx())
        assert not outcome.ok
        assert outcome.transient is True


# --- Missing-credential semantics ------------------------------------


class TestEnsureCategoriesMissingCredentials:
    """When qBit credentials aren't yet available, the lifecycle
    ensurer must surface ``Outcome.failure(transient=True)`` so the
    orchestrator retries after ``probe_has_api_key`` reaches ok.

    This is the silent-error-as-ok bug class — the legacy handler
    logged a real login failure and returned ``status=ok``, masking
    the operator-config gap. The lifecycle path is honest about it."""

    def test_transient_failure_when_no_password(self) -> None:
        from media_stack.adapters.qbittorrent import lifecycle as lc_mod
        no_default_wirer = CategoriesWirer(
            default_username="", default_password="",
        )
        with patch.object(lc_mod, "_CATEGORIES_WIRER", no_default_wirer):
            outcome = QbittorrentLifecycle().ensure_categories(
                _ctx(password=None),
            )
        assert not outcome.ok
        assert outcome.transient is True
        assert "QBIT_PASSWORD" in outcome.error

    @patch("urllib.request.build_opener")
    def test_transient_failure_on_login_network_error(
        self, mock_build,
    ) -> None:
        opener = _build_opener_returning(
            urllib.error.URLError("dns"),
        )
        mock_build.return_value = opener

        outcome = QbittorrentLifecycle().ensure_categories(_ctx())
        assert not outcome.ok
        assert outcome.transient is True
        assert "login failed" in outcome.error.lower()

    @patch("urllib.request.build_opener")
    def test_transient_failure_on_login_fails_response(
        self, mock_build,
    ) -> None:
        opener = _build_opener_returning(_http_response(b"Fails."))
        mock_build.return_value = opener

        outcome = QbittorrentLifecycle().ensure_categories(_ctx())
        assert not outcome.ok
        assert outcome.transient is True


# --- Per-call opener (cookie-jar isolation) --------------------------


class TestPerCallOpener:
    """Each ``probe`` / ``ensure`` builds a *fresh* cookie-jar
    opener — two adjacent calls don't share session state. Pinning
    this prevents a future "cache opener as instance attribute"
    refactor from masking a real auth regression as a stale-cookie
    pass."""

    @patch("urllib.request.build_opener")
    def test_each_invocation_builds_a_new_opener(self, mock_build) -> None:
        # Provide enough responses for two probe calls (login + list
        # each).
        responses = [
            _login_ok_response(),
            _http_response(_categories_json(*_DESIRED_NAMES)),
            _login_ok_response(),
            _http_response(_categories_json(*_DESIRED_NAMES)),
        ]
        # Each build_opener() call returns a separate opener, but
        # they share the same canned-response queue so we can verify
        # request count.
        iterator = iter(responses)

        def _opener_factory(*_a, **_k):
            opener = MagicMock()

            def _open(*_args, **_kwargs):
                return next(iterator)

            opener.open.side_effect = _open
            return opener

        mock_build.side_effect = _opener_factory

        lc = QbittorrentLifecycle()
        lc.probe_categories(_ctx())
        lc.probe_categories(_ctx())
        # Two separate openers must have been built — pin against a
        # cached-opener refactor.
        assert mock_build.call_count == 2


# --- CategoriesWirer constructor injection ---------------------------


class TestWirerConstructorInjection:
    """Pin the constructor surface so a "drop the kwargs in favor of
    module-globals" refactor surfaces here. Class-structure ratchet
    on the public DI surface."""

    def test_default_constructor_works(self) -> None:
        # Smoke: bare ``CategoriesWirer()`` constructs cleanly with
        # all kwargs taking their factory defaults.
        wirer = CategoriesWirer()
        assert isinstance(wirer, CategoriesWirer)

    def test_custom_desired_categories_drives_probe(self) -> None:
        # If the operator overrides the desired set (e.g. add
        # "audiobooks"), probe_failed reports the override-relative
        # missing entry.
        wirer = CategoriesWirer(
            desired_categories={"movies": "/x", "audiobooks": "/y"},
        )
        with patch("urllib.request.build_opener") as mock_build:
            mock_build.return_value = _build_opener_returning(
                _login_ok_response(),
                _http_response(_categories_json("movies")),
            )
            result = wirer.probe(_ctx())
        assert result.status == "failed"
        assert result.evidence["missing"] == ["audiobooks"]
