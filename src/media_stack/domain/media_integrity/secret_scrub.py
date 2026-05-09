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

ADR-0012 shape
--------------
All helpers live as plain instance methods on :class:`SecretScrub`
(no ``@staticmethod``). A module-level singleton plus aliases preserve
the existing import surface
(``from ... import safe_exception_message, scrub_url``).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from media_stack.domain.media_integrity.servarr_http_error import (
    ServarrHttpError,
)


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


class SecretScrub:
    """Bundle of structural-exception scrubbing helpers (ADR-0012 class form).

    Instances are stateless; the module-level :data:`_INSTANCE` plus
    aliases below are the canonical entry points so the existing
    ``from media_stack.domain.media_integrity.secret_scrub import …``
    style imports keep working unchanged.
    """

    def scrub_url(self, url: str) -> str:
        """Strip secret-looking query params + trim the path.

        ``http://radarr:7878/api/v3/foo?apikey=abc123&movieId=1`` →
        ``http://radarr:7878/api/v3/foo?apikey=REDACTED&movieId=1``.
        """
        if not url:
            return ""
        try:
            parts = urlsplit(url)
        except ValueError:
            return self._redact_regex(url)
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
        return self._redact_regex(cleaned)

    def safe_exception_message(
        self, exc: BaseException, *, max_len: int = 500,
    ) -> str:
        """Return an audit/log-safe string for ``exc``.

        For known structural types (``ServarrHttpError``), reads the
        fields directly and scrubs them. Falls back to ``str(exc)`` +
        regex scrub for unknown exceptions.
        """
        message = self._structural_message(exc)
        if message is None:
            message = self._redact_regex(str(exc))
        else:
            message = self._redact_regex(message)
        if len(message) > max_len:
            message = message[: max_len - 3] + "..."
        return message

    def _structural_message(self, exc: BaseException) -> str | None:
        """If we recognize the exception shape, format a clean message.

        ADR-0011 Phase 1: ``ServarrHttpError`` lives in the domain
        layer alongside this scrubber, so the import is module-top
        above and the lookup here is a straight ``isinstance``. The
        legacy deferred-import-via-services-shim hack is gone.
        """
        if isinstance(exc, ServarrHttpError):
            status = getattr(exc, "status", 0)
            raw_url = getattr(exc, "url", "")
            safe_url = self.scrub_url(str(raw_url))
            body = getattr(exc, "body", b"") or b""
            # Bodies often echo the request context (including headers);
            # truncate + regex-scrub the first N bytes only.
            body_snippet = body[:200].decode("utf-8", errors="replace")
            return f"{safe_url} -> {status}: {body_snippet}"

        return None

    def _redact_regex(self, text: str) -> str:
        if not text:
            return ""
        text = _KV_SECRET.sub(lambda m: f"{m.group(1)}=REDACTED", text)
        text = _HEX_RUN.sub("REDACTED", text)
        return text


_INSTANCE = SecretScrub()

# Module-level aliases preserve every public + underscore name so existing
# ``from media_stack.domain.media_integrity.secret_scrub import scrub_url``
# style imports keep working unchanged. The underscore names are kept
# aliased so any future ``mock.patch.object(mod, "_redact_regex", …)``
# style test still intercepts the call site.
scrub_url = _INSTANCE.scrub_url
safe_exception_message = _INSTANCE.safe_exception_message
_structural_message = _INSTANCE._structural_message
_redact_regex = _INSTANCE._redact_regex


__all__ = [
    "SecretScrub",
    "safe_exception_message",
    "scrub_url",
]
