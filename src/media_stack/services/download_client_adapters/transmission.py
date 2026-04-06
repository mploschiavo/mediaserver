"""Transmission bootstrap adapter (future-ready stub)."""

from __future__ import annotations

from .base import DownloadClientAdapterBase


class TransmissionDownloadClientAdapter(DownloadClientAdapterBase):
    """Future transmission adapter surface.

    The current stack does not expose transmission operations yet, so this
    adapter intentionally behaves as a no-op and reports `login_ok=False`.
    """

    def is_enabled(self) -> bool:
        return bool(self.context.configure_arr_clients)

    def prepare(self) -> None:
        # Placeholder status so pipeline callers can remain stable.
        self.context.status.setdefault("login_ok", False)
