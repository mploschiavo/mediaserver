"""Consistent redaction helpers for secret-bearing values.

Use these helpers anywhere a response body, log line, or audit entry
could otherwise echo a raw secret. Importing from one module means:

- A single place to tune the fingerprint policy (e.g. number of
  characters shown, separator choice).
- A ratchet can scan for direct secret formatting (e.g. ``str(key)``
  being sent to the client) and flag anything that doesn't go through
  these helpers.
- Tests cover the edge cases once (empty, short, unicode, binary-ish).

Rules
-----

1. **Fingerprints are one-way.** They let an operator distinguish two
   keys ("is this the one I rotated?") but do not let a caller
   reconstruct the original. Currently that's ``first4...last4``; a
   future tightening to a salted HMAC with a stable deploy-scoped
   salt stays behind this interface.

2. **Fingerprints are not security boundaries.** A determined
   attacker with the fingerprint format + knowledge of the key
   space can narrow the search. The fingerprint is a usability
   surface; the security boundary is "raw key never leaves the
   server."

3. **`redact_if_secret_key` is the conservative default.** Any
   dict-valued mapping that contains keys named like secrets (
   ``api_key``, ``apikey``, ``access_token``, ``password``,
   ``bearer``, ``secret``, ``session_token``) gets those values
   replaced with ``"[redacted]"``. Use this on logging payloads
   where you can't enumerate every possible key.

ADR-0012 shape
--------------
All helpers live as plain instance methods on :class:`SecretRedaction`
(no ``@staticmethod``). A module-level singleton plus aliases preserve
the existing import surface (``from ... import fingerprint``).
"""

from __future__ import annotations

import re
from typing import Any, Mapping

_MIN_FINGERPRINT_LEN = 12  # below this, we still show edges but with a smaller window
_FINGERPRINT_HEAD = 4
_FINGERPRINT_TAIL = 4
_FINGERPRINT_MARKER = "…"  # U+2026 HORIZONTAL ELLIPSIS — visually clearly truncation

# Case-insensitive substrings of dict-keys whose values are treated
# as secrets and auto-redacted. Matches whole keys or keys containing
# the substring (e.g. ``jellyfin_api_key``, ``stack_admin_password``).
# Conservative on purpose — false positives are safer than false
# negatives.
_SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "api-key",
    "access_token",
    "accesstoken",
    "access-token",
    "refresh_token",
    "bearer",
    "password",
    "secret",
    "session_token",
    "auth_token",
    "private_key",
    "client_secret",
)

_REDACTED_PLACEHOLDER = "[redacted]"

# A regex to spot accidental embedded secrets in free-text — useful
# for scrubbing logged URLs like ``https://host/api?api_key=XYZ``.
# Matches ``api_key=<non-space>`` and similar. Not exhaustive; the
# `redact_if_secret_key` path covers structured payloads.
_URL_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|apikey|access[_-]?token|bearer|password|secret)"
    r"=([^&\s]+)",
)


class SecretRedaction:
    """Bundle of redaction helpers (ADR-0012 class form).

    Instances are stateless; the module-level :data:`_INSTANCE` plus
    aliases below are the canonical entry points so the existing
    ``from ... import fingerprint`` API stays unchanged.
    """

    def fingerprint(self, value: str) -> str:
        """Return a short non-reversible-enough identifier for a secret.

        Empty input returns the empty string. Anything under 8 chars is
        hidden entirely because showing ``abc...abc`` for a 6-char
        secret trivially leaks it.
        """
        if not value:
            return ""
        if len(value) < 8:
            return _REDACTED_PLACEHOLDER
        if len(value) < _MIN_FINGERPRINT_LEN:
            head_tail = 2
            return f"{value[:head_tail]}{_FINGERPRINT_MARKER}{value[-head_tail:]}"
        return (
            f"{value[:_FINGERPRINT_HEAD]}"
            f"{_FINGERPRINT_MARKER}"
            f"{value[-_FINGERPRINT_TAIL:]}"
        )

    def redact_api_key_map(
        self,
        keys: Mapping[str, str],
        *,
        source: str = "",
    ) -> dict[str, dict[str, Any]]:
        """Turn ``{service_id: raw_key}`` into a safe-to-echo shape.

        Each service gets ``{has_key, fingerprint, source}`` — the raw
        key is never in the returned structure. Use for any response
        body or log line that needs to describe the key surface.

        ``source`` is an optional label describing where the key came
        from (``"file"``, ``"env"``, ``"sqlite"``) so the admin UI can
        distinguish a rotated-on-disk key from an env-var fallback.
        """
        out: dict[str, dict[str, Any]] = {}
        for svc_id, raw in keys.items():
            value = raw or ""
            out[svc_id] = {
                "has_key": bool(value),
                "fingerprint": self.fingerprint(value) if value else "",
                "source": source,
            }
        return out

    def redact_if_secret_key(self, payload: Any) -> Any:
        """Recursively redact secret-named fields in an arbitrary payload.

        For dicts: any key matching ``_SECRET_KEY_PATTERNS`` (case
        insensitive, substring) has its value replaced with
        ``"[redacted]"``. Keys that aren't in that list are recursed
        into. For lists: recurse into each element. For anything else:
        return as-is.

        Used to scrub log lines / telemetry / debug snapshots where an
        explicit field-by-field redaction isn't practical.
        """
        if isinstance(payload, Mapping):
            out: dict[str, Any] = {}
            for k, v in payload.items():
                if self._looks_secret(k):
                    out[k] = _REDACTED_PLACEHOLDER
                else:
                    out[k] = self.redact_if_secret_key(v)
            return out
        if isinstance(payload, list):
            return [self.redact_if_secret_key(item) for item in payload]
        if isinstance(payload, tuple):
            return tuple(self.redact_if_secret_key(item) for item in payload)
        return payload

    def _looks_secret(self, key: Any) -> bool:
        """True if the (dict-key) name matches a known secret pattern.

        We don't use regex for simplicity and speed — these are fixed
        substring tests run in a tight loop during logging.
        """
        if not isinstance(key, str):
            return False
        k = key.lower()
        for pat in _SECRET_KEY_PATTERNS:
            if pat in k:
                return True
        return False

    def redact_url_query(self, text: str) -> str:
        """Replace ``?api_key=...`` and similar in any text with
        ``?api_key=[redacted]``. Use on URLs flowing into audit / log
        entries — some outbound HTTP calls still carry secrets as query
        params (tracked for fix; see ``docs/security-a11y-contract.md``
        § 3).
        """
        if not text:
            return text
        return _URL_SECRET_RE.sub(
            lambda m: f"{m.group(1)}={_REDACTED_PLACEHOLDER}", text,
        )


_INSTANCE = SecretRedaction()

# Module-level aliases preserve every public name so existing
# ``from media_stack.domain.auth.secret_redaction import fingerprint``
# style imports keep working unchanged.
fingerprint = _INSTANCE.fingerprint
redact_api_key_map = _INSTANCE.redact_api_key_map
redact_if_secret_key = _INSTANCE.redact_if_secret_key
redact_url_query = _INSTANCE.redact_url_query


__all__ = [
    "SecretRedaction",
    "fingerprint",
    "redact_api_key_map",
    "redact_if_secret_key",
    "redact_url_query",
]
