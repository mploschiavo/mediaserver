"""Watched-lookup adapter for the cleanup ``watched_first`` strategy.

Decorates a torrent-candidate list with a ``_watched`` boolean by
querying Jellyfin's ``UserData.Played`` table. Lifted out of
``disk_guardrails_service.py`` so that file stays under the 400-line
hygiene ratchet while the cleanup-policy + storage-event-publisher
modules grow.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from media_stack.core.logging_utils import log_swallowed


_log = logging.getLogger("media_stack.disk_guardrails.watched_lookup")


class WatchedLookupAdapter:
    """Adapter that decorates a candidate list with a ``_watched``
    bool by querying Jellyfin's ``UserData.Played`` table.

    Class-based so the no-loose-functions ratchet stays clean and so
    tests can swap in a deterministic stub. Constructor-injects the
    lookup function; default path resolves to a Jellyfin media-server
    adapter at call time, and on any failure (no Jellyfin reachable,
    no api key, lookup raises) returns the candidates untouched +
    emits a single INFO line so the cleanup pass continues with
    ``oldest_first`` semantics.
    """

    def __init__(
        self,
        *,
        lookup_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._lookup = lookup_fn
        self._log = log_fn

    def decorate(
        self, candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        """Return ``(candidates_with_watched, ok)``.

        ``ok=True`` means every candidate received a ``_watched`` bool;
        the caller can sort with the ``watched_first`` strategy safely.
        ``ok=False`` means the lookup failed wholesale and the caller
        should fall back to ``oldest_first``.
        """
        if self._lookup is None:
            try:
                from media_stack.services.media_server_adapters.factory import (
                    get_media_server_adapter,
                )
                adapter = get_media_server_adapter("jellyfin")
                played_lookup = getattr(adapter, "is_played_for_paths", None)
            except (ImportError, AttributeError, OSError, ValueError) as exc:
                self._emit_log(
                    f"[INFO] watched_first lookup failed; falling back to "
                    f"oldest_first ({exc})"
                )
                return candidates, False
            if played_lookup is None:
                self._emit_log(
                    "[INFO] watched_first lookup failed; falling back to "
                    "oldest_first (jellyfin adapter has no is_played_for_paths)"
                )
                return candidates, False
            lookup = played_lookup
        else:
            lookup = self._lookup
        try:
            decorated = lookup(candidates)
        except (OSError, ValueError, RuntimeError) as exc:
            log_swallowed(exc, context="watched_first_lookup")
            self._emit_log(
                f"[INFO] watched_first lookup failed; falling back to "
                f"oldest_first ({exc})"
            )
            return candidates, False
        if not isinstance(decorated, list):
            return candidates, False
        return decorated, True

    def _emit_log(self, message: str) -> None:
        if self._log is not None:
            self._log(message)
            return
        _log.info(message)


__all__ = ["WatchedLookupAdapter"]
