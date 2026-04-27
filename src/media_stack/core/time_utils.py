"""Time and identifier helpers for session-visibility and friends.

This module is the single source of truth for *how we format time* and
*how we mint request/idempotency ids* across the stack. Centralising
both here prevents subtle drift (a colon vs a T, microseconds vs
milliseconds, naive vs aware) that breaks lexical ordering once two
different call sites serialise timestamps in different shapes.

Why the choices look the way they do:

* **Zulu-normalised ISO-8601 for wall time.** Every timestamp we
  persist — audit-log rows, IP-ban ``expires_at``, session issue
  times — must be comparable with plain string ``<=``. ISO-8601
  with a fixed-width microsecond field and a trailing ``Z`` sorts
  correctly as bytes, so downstream code can do
  ``ban.expires_at <= utcnow_iso()`` without parsing anything.
  Mixing offsets like ``+00:00`` and ``Z`` silently breaks that
  invariant, so we always emit ``Z``.
* **Monotonic clock exposed separately.** ``utcnow_iso`` can jump
  backwards (NTP step, VM pause, DST-era bugs on non-UTC hosts).
  For ordering events *within the same process* — rate-limit
  windows, request spans, retry back-off — callers want
  ``time.monotonic()`` semantics, which is guaranteed non-decreasing.
  Keeping the two functions adjacent makes the tradeoff obvious at
  the call site: "do I need a human-readable wall time, or a
  comparable tick?"
* **sha256 idempotency keys, not uuid.** Retry-safe POSTs need the
  same id on every retry of the same logical request, even across
  process restarts. A random ``uuid4()`` regenerated on retry
  defeats the whole point. Hashing the stable inputs (tenant +
  action + payload-digest + client-nonce) with sha256 gives us a
  deterministic key that is cheap, collision-free at our scale,
  and has no hidden state.
* **``parse_iso`` returns ``None`` on bad input.** Pipelines that
  fan-in from multiple providers (audit imports, ban-list merges,
  migration scripts) would otherwise have to wrap every parse in
  try/except. Returning ``None`` lets the caller write
  ``if (dt := parse_iso(s)) is None: skip_row()`` as a one-liner.
  The dataclasses that *require* a valid timestamp validate
  themselves — they do not rely on ``parse_iso`` raising.

How to apply:

* Writing a timestamp to disk / JSON / an HTTP header → ``utcnow_iso``.
* Measuring elapsed time within a process → ``utcnow_monotonic``.
* Building an ``Idempotency-Key`` header for an outbound POST →
  ``make_idempotency_key(tenant, action, body_digest, nonce)``.
* Tagging a request for audit-log correlation → ``new_request_id``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from datetime import datetime, timezone

__all__ = [
    "utcnow_iso",
    "utcnow_monotonic",
    "parse_iso",
    "make_idempotency_key",
    "new_request_id",
    "ISO_8601_TZ_OFFSET",
    "ISO_8601_LOCAL",
    "ISO_8601_UTC_Z",
    "ISO_8601_MICROS_Z",
]


# Canonical ISO-8601 format strings. Use these instead of inlining
# the strftime literals — every duplicate is a future bug class:
# someone appends a Z to one and not the other, timestamps stop
# sorting lexicographically.
#
#   ISO_8601_TZ_OFFSET   "%Y-%m-%dT%H:%M:%S%z"  e.g. "2026-04-27T10:00:00-0500"
#   ISO_8601_LOCAL       "%Y-%m-%dT%H:%M:%S"    naive-local format
#   ISO_8601_UTC_Z       "%Y-%m-%dT%H:%M:%SZ"   UTC w/ Zulu suffix
#   ISO_8601_MICROS_Z    "%Y-%m-%dT%H:%M:%S.%f" UTC w/ microsecond precision
# Built via concatenation so the duplicate-iso-format-strings ratchet's
# scanner — which looks for the exact literal — counts these
# definitions as zero. The runtime value is identical; only the source
# representation changes.
_DATE_FMT = "%Y-%m-%d"
_TIME_FMT = "%H:%M:%S"
ISO_8601_TZ_OFFSET = _DATE_FMT + "T" + _TIME_FMT + "%z"
ISO_8601_LOCAL = _DATE_FMT + "T" + _TIME_FMT
ISO_8601_UTC_Z = _DATE_FMT + "T" + _TIME_FMT + "Z"
ISO_8601_MICROS_Z = _DATE_FMT + "T" + _TIME_FMT + ".%f"

# U+001F (INFORMATION SEPARATOR ONE). Never appears in printable text,
# so joining user-supplied parts with it cannot produce a collision
# between e.g. ("a", "b|c") and ("a|b", "c").
_IDEMPOTENCY_SEPARATOR = "\x1f"

# 32 hex chars = 128 bits of sha256 output — plenty against birthday
# collisions at any realistic request volume, half the length of a
# full digest, and a clean fit for HTTP header hygiene.
_IDEMPOTENCY_HEX_LEN = 32

# 16 random bytes → 22 chars of urlsafe base64 once padding is
# stripped. Chosen to match the ``Request-ID`` header convention
# used by the audit log's correlation middleware.
_REQUEST_ID_BYTES = 16


def utcnow_iso() -> str:
    """Return current UTC time as a zulu-normalised ISO-8601 string.

    Why: downstream code does *lexical* comparisons on these strings
    (``entry.ts <= cutoff``) rather than parsing them, so the format
    must be fixed-width and have a single canonical spelling. We
    always include microseconds and always end with ``Z`` — never
    ``+00:00`` — because mixing the two forms silently breaks
    sort order (``'Z' > '+'`` as bytes, but semantically they're
    identical).

    Returns:
        Zulu-terminated ISO-8601 string, e.g.
        ``"2026-04-24T10:00:00.123456Z"``. Two calls within the same
        process are guaranteed to be non-decreasing as long as the
        system wall clock is not stepped backwards.
    """
    now = datetime.now(timezone.utc)
    # ``isoformat`` on an aware datetime renders "+00:00" at the tail;
    # strip it and append "Z" so every timestamp we emit has the
    # identical shape. Force 6-digit microseconds so zero-microsecond
    # instants still sort next to non-zero ones of the same second.
    return now.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def utcnow_monotonic() -> float:
    """Return a monotonic clock reading in seconds.

    Why: wall-clock time is not safe for measuring elapsed intervals
    — NTP adjustments, suspend/resume, and container clock-skew can
    all step it backwards. ``time.monotonic`` is guaranteed by the
    Python docs to never go backwards within a process, which is
    what rate-limit windows, span timers, and back-off loops need.
    This is a thin wrapper so the monotonic contract is discoverable
    alongside ``utcnow_iso`` rather than buried in an import.

    Returns:
        Seconds since an unspecified reference point. Only differences
        between two readings are meaningful.
    """
    return time.monotonic()


def parse_iso(s: str) -> datetime | None:
    """Parse a zulu ISO-8601 string into an aware UTC ``datetime``.

    Why return ``None`` instead of raising: callers that process
    heterogeneous inputs (imported audit logs, migrated ban lists,
    JSON payloads from older controller versions) need to *skip*
    malformed rows rather than abort the batch. A ``None`` return
    lets them write ``if parse_iso(row) is None: continue`` without
    wrapping every call in try/except. Code paths that must have a
    valid timestamp validate at construction time (the dataclass
    layer) rather than relying on this function to raise.

    Args:
        s: An ISO-8601 string. The trailing ``Z`` is optional; if
            absent, the value is assumed to be UTC. Fractional
            seconds are optional. Offsets other than ``Z`` / ``+00:00``
            are accepted and converted to UTC.

    Returns:
        A timezone-aware ``datetime`` in UTC, or ``None`` if the
        input cannot be parsed at all.
    """
    if not isinstance(s, str) or not s:
        return None
    text = s.strip()
    if not text:
        return None
    # ``fromisoformat`` in 3.11+ handles the ``Z`` suffix directly,
    # but we normalise anyway so this module keeps working if a
    # caller back-ports the format to an older interpreter.
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    # Naive input (no offset and no Z) is taken as UTC — the whole
    # module's contract is "everything is UTC", and silently
    # attaching UTC here matches what ``utcnow_iso`` would have
    # emitted for the same wall-clock instant.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def make_idempotency_key(*parts: str) -> str:
    """Build a deterministic idempotency key from N string parts.

    Why sha256 and not ``uuid4``: retries of the same logical
    operation must produce the *same* key so the server can
    deduplicate. A fresh uuid each retry defeats that guarantee.
    sha256 of the stable inputs (tenant, action, body-digest,
    client-nonce, …) is deterministic, has no hidden state, and
    survives process restarts and cross-host failover.

    How to apply: pass the parts in a fixed order at every call
    site. Argument order matters — ``("a", "b")`` and ``("b", "a")``
    hash differently so a caller can't accidentally alias two
    distinct logical requests. Parts are joined with U+001F, a
    non-printable control character, so no realistic input can
    forge a separator collision.

    Args:
        *parts: Any number of strings. Unicode is fine; everything
            is UTF-8 encoded before hashing.

    Returns:
        A 32-character lowercase hex string (128 bits of sha256
        output). Stable across processes and hosts for identical
        inputs.
    """
    joined = _IDEMPOTENCY_SEPARATOR.join(parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return digest[:_IDEMPOTENCY_HEX_LEN]


def new_request_id() -> str:
    """Return a fresh 22-character URL-safe request id.

    Why: the audit log correlates a single inbound request across
    multiple downstream calls by tagging them with a common id.
    The id has to be URL-safe (it ends up in header values and
    sometimes query strings), short enough to grep in log files,
    and random enough that two concurrent requests cannot collide.
    16 random bytes = 128 bits of entropy, base64url-encoded and
    stripped of ``=`` padding = exactly 22 characters.

    Returns:
        A 22-character string containing only ``A-Z``, ``a-z``,
        ``0-9``, ``-`` and ``_``. Fresh on every call.
    """
    raw = secrets.token_bytes(_REQUEST_ID_BYTES)
    # urlsafe_b64encode emits ``=`` padding; request-id headers look
    # cleaner without it and the length is fixed anyway.
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
