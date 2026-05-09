"""SSE filter + line-formatting helpers for the controller log stream.

The Logs page polls ``/api/logs/{source}`` for non-controller services (which
are paged by ``kubectl logs --tail=N`` on a 3-second cadence), but the
controller's own ring buffer is a sequence-numbered stream the SSE endpoint
``/api/logs/stream`` taps directly via ``state.wait_for_log`` -> push. This
module isolates the *pure* filtering and formatting decisions so the
HTTP-loop itself stays tiny and the predicates are unit-testable in
isolation.

Filter precedence (matches ``ops.get_service_logs`` for parity):

  1. ``action`` — exact match on the per-line action tag (sourced
     from ``runtime_platform.current_action_tag`` post-Phase-5c.4c)
  2. ``level``  — case-insensitive token match against the line body
  3. ``q``      — free text or ``/regex/i`` via the standard delimiter

Lines that pass every active filter are forwarded; anything else is
dropped before it reaches the wire. ``None`` / empty string for a filter
means "don't filter on this dimension".

ADR-0012: the public helpers are instance methods of
``LogsSseService``. Module-level callables are bound-method aliases
on the singleton ``_INSTANCE`` so existing imports (and any
``mock.patch`` of those names) continue to work without churn.
"""

from __future__ import annotations

import json
import re
import time
from typing import Mapping

LEVEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "error": re.compile(r"\b(ERROR|ERR|FATAL|CRIT|CRITICAL)\b", re.IGNORECASE),
    "warning": re.compile(r"\b(WARN|WARNING)\b", re.IGNORECASE),
    "info": re.compile(r"\b(INFO|NOTICE)\b", re.IGNORECASE),
    "debug": re.compile(r"\b(DEBUG|DBG|TRACE)\b", re.IGNORECASE),
}


class LogsSseService:
    """Pure-function SSE filter/format helpers grouped on a service object.

    No I/O, no shared state — the methods are deterministic transforms
    over the per-line inputs. The class exists so the module follows
    ADR-0012 (class-based services with constructor-injected deps;
    loose top-level functions are the legacy pattern). Construction
    takes the level-pattern table so a future caller could swap it for
    tests, but the default singleton uses ``LEVEL_PATTERNS`` directly.
    """

    def __init__(
        self,
        level_patterns: Mapping[str, re.Pattern[str]] = LEVEL_PATTERNS,
    ) -> None:
        self._level_patterns = level_patterns

    def compile_q(self, q: str | None) -> re.Pattern[str] | None:
        """Compile a free-text or ``/regex/i`` filter. Empty -> ``None``.

        The ``/foo/i`` and ``/foo/`` forms map to a Python regex (case
        insensitive when the trailing ``i`` flag is present). Anything else
        is escaped into a literal substring match.
        """
        if not q:
            return None
        if len(q) >= 2 and q.startswith("/") and q.rstrip("i").endswith("/"):
            flags = re.IGNORECASE if q.endswith("i") else 0
            body = q[1:-2] if q.endswith("i") else q[1:-1]
            try:
                return re.compile(body, flags)
            except re.error:
                # Fall through to literal match — better to surface SOMETHING
                # than blow up the SSE loop on an operator typo.
                return re.compile(re.escape(q), re.IGNORECASE)
        return re.compile(re.escape(q), re.IGNORECASE)

    def should_emit_log_line(
        self,
        msg: str,
        action_field: str,
        *,
        action_filter: str | None = None,
        level_filter: str | None = None,
        q_pattern: re.Pattern[str] | None = None,
    ) -> bool:
        """Return True iff ``msg`` should be sent to the SSE consumer."""
        if action_filter and action_field != action_filter:
            return False
        if level_filter:
            pattern = self._level_patterns.get(level_filter.lower())
            if pattern is not None and not pattern.search(msg):
                return False
        if q_pattern is not None and not q_pattern.search(msg):
            return False
        return True

    def format_sse_event(
        self, seq: int, ts: float, msg: str, action: str,
    ) -> bytes:
        """Encode a single log entry as an SSE ``id: / data:`` block.

        The payload is JSON so the UI can treat it like any other typed
        record. ``ts`` is ISO-8601 in *local time* to match the polling
        handler's existing format — the operator should not see two clocks.
        """
        iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
        payload = json.dumps(
            {"seq": seq, "ts": iso, "msg": msg, "action": action},
            separators=(",", ":"),
        )
        return f"id: {seq}\ndata: {payload}\n\n".encode()


# Singleton + module-level aliases. Every public name keeps the
# pre-refactor signature so importers (and ``mock.patch`` calls in the
# test suite) don't churn.
_INSTANCE = LogsSseService()

compile_q = _INSTANCE.compile_q
should_emit_log_line = _INSTANCE.should_emit_log_line
format_sse_event = _INSTANCE.format_sse_event
