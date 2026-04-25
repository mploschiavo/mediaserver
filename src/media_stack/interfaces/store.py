"""Persistence-store port.

Generalises the JSON / YAML / SQLite stores currently scattered
across ``core/`` and ``services/``. A store is a typed
key/value-of-records persistence surface — it does NOT model a
relational database. Use cases get a typed view; the concrete
backend (file / SQLite / Redis / in-memory) is wired by the
composition root.

Phase 16-A scaffolding: shape only. Phase 16-E migrates the live
stores behind this port.
"""

from __future__ import annotations

from typing import Generic, Iterator, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class Store(Protocol, Generic[T]):
    """Port for a typed key/value store of records.

    Keys are opaque strings; values are ``T``. The port intentionally
    omits transactions / batching / queries — those are concerns
    for a richer port that derives from this one if/when a use case
    needs them.
    """

    name: str

    def get(self, key: str) -> T | None:
        """Return the record at ``key`` or ``None`` if absent."""

    def put(self, key: str, value: T) -> None:
        """Persist ``value`` at ``key``. Overwrites existing."""

    def delete(self, key: str) -> bool:
        """Remove the record at ``key``. Returns True if removed,
        False if it was absent. Idempotent."""

    def keys(self) -> Iterator[str]:
        """Iterate over all keys in arbitrary order."""
