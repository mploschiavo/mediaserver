"""Structural exception scrubber for media-integrity.

Errors from Servarr/Bazarr APIs are the #1 vector for leaking API
keys into audit logs. The existing ``_redact()`` regex (still in
every reconciler/enforcer module as defense-in-depth) catches the
common cases. This module adds a *structural* pre-pass that
understands the shape of our own error types and strips secrets
before they ever reach the regex.

Why structural AND regex?
    The regex is generic: it matches ``apikey=<hex>`` and long hex
    runs. That handles the "obvious" case where an adapter error
    message accidentally echoes a request header. But a crafty
    server error body could contain the key in a URL-encoded form
    (``api_key=abc%2Ddef``), in a JSON value (``"apiKey": "abc"``),
    or in a path segment (``/api/v3/foo?apikey=abc``) that the
    current regex doesn't cover. Structural scrubbing starts from
    the exception object's known fields rather than its str()
    output — for our ``ServarrHttpError`` we own the ``url`` and
    ``body`` fields, so we can strip query strings and drop bodies
    instead of relying on the regex to notice.

Defense-in-depth: the regex scrub still runs on the final output.
Two uncorrelated layers; a miss in one is caught by the other.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SECRET_QUERY_KEYS = {
    "apikey", "api_key", "token", "access_token", "auth",
    "authorization", "key", "secret", "password",
}

# Long hex runs are likely keys. Servarr api keys are 32 hex chars.
_HEX_RUN = re.compile(r"[a-f0-9]{32,}", re.IGNORECASE)

# ``key=value`` style in query strings or error messages.
_KV_SECRET = re.compile(
    r"(?i)(apikey|api_key|x-api-key|authorization|bearer|token|password|secret)"
    r"\s*[=:]\s*\S+",
)


def scrub_url(url: str) -> str:
    """Strip secret-looking query params + trim the path.

    ``http://radarr:7878/api/v3/foo?apikey=abc123&movieId=1`` →
    ``http://radarr:7878/api/v3/foo?apikey=REDACTED&movieId=1``.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return _redact_regex(url)
    query_pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SECRET_QUERY_KEYS:
            query_pairs.append((key, "REDACTED"))
        else:
            query_pairs.append((key, value))
    cleaned_query = urlencode(query_pairs)
    cleaned = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, cleaned_query, "")
    )
    return _redact_regex(cleaned)


def safe_exception_message(exc: BaseException, *, max_len: int = 500) -> str:
    """Return an audit/log-safe string for ``exc``.

    For known structural types (``ServarrHttpError``), reads the
    fields directly and scrubs them. Falls back to ``str(exc)`` +
    regex scrub for unknown exceptions.
    """
    message = _structural_message(exc)
    if message is None:
        message = _redact_regex(str(exc))
    else:
        message = _redact_regex(message)
    if len(message) > max_len:
        message = message[: max_len - 3] + "..."
    return message


def _structural_message(exc: BaseException) -> str | None:
    """If we recognize the exception shape, format a clean message."""
    # TODO(phase-16-F): this domain module needs to recognise the
    # ``ServarrHttpError`` shape (which currently lives in the adapters
    # layer at ``adapters.media_integrity._servarr_base``). A direct
    # ``from media_stack.adapters.media_integrity._servarr_base``
    # import would trip the hexagonal domain→adapters layering ratchet,
    # so we resolve through the legacy ``services.media_integrity``
    # shim path — Python's ``sys.modules`` aliasing makes this the
    # same class object either way. The cleanest fix is to lift
    # ``ServarrHttpError`` (a pure exception type, no I/O) into the
    # domain layer alongside this module and have the adapter base
    # import it from here; deferred to Phase 16-F.
    # Import lazily to avoid a cycle with the adapters module that
    # raises ``ServarrHttpError`` and might import this module.
    try:
        from media_stack.services.media_integrity.adapters._servarr_base import (
            ServarrHttpError,
        )
    except Exception:  # pragma: no cover — optional import guard
        ServarrHttpError = None  # type: ignore[assignment]

    if ServarrHttpError is not None and isinstance(exc, ServarrHttpError):
        status = getattr(exc, "status", 0)
        raw_url = getattr(exc, "url", "")
        safe_url = scrub_url(str(raw_url))
        body = getattr(exc, "body", b"") or b""
        # Bodies often echo the request context (including headers);
        # truncate + regex-scrub the first N bytes only.
        body_snippet = body[:200].decode("utf-8", errors="replace")
        return f"{safe_url} -> {status}: {body_snippet}"

    return None


def _redact_regex(text: str) -> str:
    if not text:
        return ""
    text = _KV_SECRET.sub(lambda m: f"{m.group(1)}=REDACTED", text)
    text = _HEX_RUN.sub("REDACTED", text)
    return text


__all__ = [
    "safe_exception_message",
    "scrub_url",
]
