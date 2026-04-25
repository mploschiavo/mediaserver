"""Base adapter port.

Every concrete adapter (``adapters/jellyfin/``, ``adapters/sonarr/``,
``adapters/k8s/``, …) implements ``Adapter``. The protocol is
deliberately small — lifecycle plus identity plus health. Per-tech
ports refine it (see ``media_server.py``, ``arr.py``).

Phase 16-A scaffolding: this protocol is not yet implemented by
anything. The first implementor lands in Phase 16-B.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Result of an adapter ``health()`` probe.

    ``ok`` is the binary signal callers normally branch on. ``detail``
    is a short human-readable string for logs/UI; ``severity`` lets
    rollups distinguish "transient" from "broken" without parsing the
    detail string.
    """

    ok: bool
    detail: str = ""
    severity: str = "info"  # one of: "info", "warning", "error"


@runtime_checkable
class Adapter(Protocol):
    """Base port for any external-system adapter.

    An adapter:

    * Has a stable ``name`` (matches the entry-point id where
      applicable; e.g. ``"jellyfin"``, ``"sonarr"``).
    * Runs lifecycle hooks ``startup()`` / ``shutdown()`` from the
      composition root. Both are idempotent.
    * Reports liveness via ``health()`` — fast, side-effect-free.
    """

    name: str

    def startup(self) -> None:
        """Acquire resources (open connections, warm caches).

        Idempotent. Safe to call again after ``shutdown()``.
        """

    def shutdown(self) -> None:
        """Release resources. Idempotent."""

    def health(self) -> HealthStatus:
        """Probe the adapter. MUST be fast and side-effect-free."""
