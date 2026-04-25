"""Tests for ``media_integrity._secret_scrub`` — structural
exception scrubbing.

Every path an adapter error could take out of the subsystem (audit
log, event bus, log line, UI error surface) should run through
``safe_exception_message``. This file pins the behavior on the
known shapes and a few adversarial strings.
"""

from __future__ import annotations

import pytest

from media_stack.services.media_integrity._secret_scrub import (
    safe_exception_message,
    scrub_url,
)
from media_stack.services.media_integrity.adapters._servarr_base import (
    ServarrHttpError,
)


# ---------------------------------------------------------------------------
# scrub_url
# ---------------------------------------------------------------------------


def test_scrub_url_replaces_apikey_query_value() -> None:
    raw = "http://radarr:7878/api/v3/moviefile?movieId=42&apikey=deadbeefdeadbeefdeadbeefdeadbeef"
    cleaned = scrub_url(raw)
    assert "deadbeef" not in cleaned
    assert "apikey=REDACTED" in cleaned
    assert "movieId=42" in cleaned


def test_scrub_url_case_insensitive_secret_keys() -> None:
    raw = "http://x/y?APIKEY=abc123&API_Key=def456&TOKEN=ghi"
    cleaned = scrub_url(raw)
    # The original case of the key is preserved but value is REDACTED.
    assert "abc123" not in cleaned
    assert "def456" not in cleaned
    assert "ghi" not in cleaned


def test_scrub_url_leaves_non_secret_query_intact() -> None:
    raw = "http://x/y?seriesId=10&season=1"
    assert scrub_url(raw) == raw


def test_scrub_url_handles_empty_string() -> None:
    assert scrub_url("") == ""


def test_scrub_url_strips_fragment() -> None:
    # Fragments never reach the server but could leak if they appear
    # in an error string; strip as defence-in-depth.
    raw = "http://x/y?a=1#fragment"
    assert "#fragment" not in scrub_url(raw)


def test_scrub_url_still_redacts_hex_in_path() -> None:
    """If a long hex run ends up in a URL path, the regex fallback
    still catches it."""
    raw = "http://x/api/v3/_debug/deadbeefdeadbeefdeadbeefdeadbeef"
    cleaned = scrub_url(raw)
    assert "deadbeef" not in cleaned


def test_scrub_url_invalid_url_falls_back_to_regex() -> None:
    raw = "not a url apikey=abc123"
    cleaned = scrub_url(raw)
    assert "abc123" not in cleaned


# ---------------------------------------------------------------------------
# safe_exception_message
# ---------------------------------------------------------------------------


def test_safe_exception_message_servarr_http_error_scrubs_url_and_body() -> None:
    exc = ServarrHttpError(
        401,
        "http://radarr:7878/api/v3/movie?apikey=deadbeefdeadbeefdeadbeefdeadbeef",
        b"401: Invalid API key deadbeefdeadbeefdeadbeefdeadbeef",
    )
    msg = safe_exception_message(exc)
    assert "401" in msg
    assert "deadbeef" not in msg
    assert "REDACTED" in msg


def test_safe_exception_message_plain_exception_regex_scrubs() -> None:
    exc = RuntimeError("connection refused apikey=abc123def456ghi789jkl012mno345pq")
    msg = safe_exception_message(exc)
    assert "abc123" not in msg


def test_safe_exception_message_includes_value_preview() -> None:
    """The message surfaces enough detail for debugging (status, host)
    without the secret."""
    exc = ServarrHttpError(
        503, "http://sonarr:8989/api/v3/episode?seriesId=10", b"unavailable",
    )
    msg = safe_exception_message(exc)
    assert "503" in msg
    assert "sonarr" in msg
    assert "unavailable" in msg


def test_safe_exception_message_truncates_long_output() -> None:
    long_body = ("x" * 5000).encode()
    exc = ServarrHttpError(500, "http://x/y", long_body)
    msg = safe_exception_message(exc, max_len=100)
    assert len(msg) <= 100
    assert msg.endswith("...")


def test_safe_exception_message_respects_custom_max_len() -> None:
    exc = RuntimeError("x" * 5000)
    short = safe_exception_message(exc, max_len=50)
    assert len(short) <= 50


def test_safe_exception_message_strips_bearer_token() -> None:
    exc = RuntimeError("401 Bearer: eyJabcdef.eyJabcdef0123456789abcdef0123456")
    msg = safe_exception_message(exc)
    assert "eyJabcdef.eyJabcdef0123456789abcdef0123456" not in msg


def test_safe_exception_message_strips_x_api_key_header_form() -> None:
    exc = RuntimeError("request failed; X-Api-Key: abc123def456ghi789jkl012mno345pq")
    msg = safe_exception_message(exc)
    assert "abc123def456ghi789jkl012mno345pq" not in msg


def test_safe_exception_message_empty_exception_safe() -> None:
    """A blank error string shouldn't crash."""
    exc = RuntimeError("")
    msg = safe_exception_message(exc)
    assert isinstance(msg, str)


def test_safe_exception_message_does_not_leak_url_apikey_in_path() -> None:
    """URL-path-embedded key (rare but seen with legacy Servarr)."""
    exc = ServarrHttpError(
        500,
        "http://radarr:7878/api/v3/movie/?apikey=abcdef0123abcdef0123abcdef0123ab",
        b"internal server error",
    )
    msg = safe_exception_message(exc)
    assert "abcdef0123abcdef0123abcdef0123ab" not in msg


def test_safe_exception_message_handles_password_kv() -> None:
    exc = RuntimeError("authentication failure password=hunter2abc123xyz")
    msg = safe_exception_message(exc)
    assert "hunter2abc123xyz" not in msg


def test_safe_exception_message_preserves_innocent_numbers() -> None:
    """Short integers (ids, ports, status codes) must not be
    mistaken for secrets."""
    exc = RuntimeError("failed to reach radarr:7878 after 5 attempts (status 503)")
    msg = safe_exception_message(exc)
    assert "7878" in msg
    assert "5 attempts" in msg
    assert "503" in msg
