"""``ServarrHttpError`` — pure exception type for non-2xx responses
from Servarr APIs.

Lives in the domain layer because:

* It is a pure value type — no I/O, no platform deps.
* The structural-scrub helper (``secret_scrub._structural_message``)
  needs to recognise its shape to redact secrets cleanly. ADR-0011
  Phase 1 made that recognition module-top by lifting the class
  here so ``domain → adapters`` deferred-import (the legacy escape
  hatch) is gone.

The adapter base
(``adapters.media_integrity._servarr_base``) re-exports the class
under its old import path for backwards compatibility with adapter
code that imported from there.
"""

from __future__ import annotations


class ServarrHttpError(RuntimeError):
    """Non-2xx response from a Servarr API. Adapter surfaces this
    rather than swallowing; the enforcer/reconciler attaches it to
    a ``failures`` entry so the UI can show "couldn't reach X".
    """

    def __init__(self, status: int, url: str, body: bytes) -> None:
        self.status = status
        self.url = url
        self.body = body
        snippet = body[:200].decode("utf-8", errors="replace")
        super().__init__(f"{url} -> {status}: {snippet}")


__all__ = ["ServarrHttpError"]
