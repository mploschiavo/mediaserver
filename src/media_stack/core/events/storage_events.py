"""Typed domain events for the disk-pressure guardrails (ADR-0008
Phase 4).

Three events project the lockdown + cleanup transitions onto the
shared ``EventBus``. The UI's ``EventStreamProvider`` already routes
``storage.*`` topics to a query invalidation on ``["storage"]``; the
classes here finally light up that branch (Phase 3 wired the listener,
Phase 4 wires the publisher).

Why three events instead of one ``StorageStateChanged``? The same
reasoning that drove the Jobs split applies: each class carries the
exact required fields for its transition, so consumers don't
defensively probe ``if .deleted is not None``. Lockdown engage and
release each have asymmetric payloads (``paused_clients`` /
``released_clients``); cleanup invocation is its own animal.

Frozen + ``kw_only`` matches the rest of the bus contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from media_stack.core.events.bus import Event


@dataclass(frozen=True, kw_only=True)
class StorageLockdownEngaged(Event):
    """A lockdown engage transition completed.

    Fires after ``DownloadLockdownService.engage()`` has persisted the
    state file. ``trigger`` distinguishes the auto-tick path
    (``"auto"``) from the operator-clicked path (``"manual"``);
    ``engaged_by`` carries the audit-friendly actor string
    (``"auto:disk-78%"`` or ``"operator:matthew"``). The
    ``paused_clients`` tuple lists adapter ``client_id``s that were
    successfully paused — failures are NOT enumerated here (they live
    on the state file's ``last_failures`` for the GET status route).
    """

    EVENT_TYPE: ClassVar[str] = "storage.lockdown_engaged"

    trigger: str
    engaged_by: str
    paused_clients: tuple[str, ...] = ()
    engaged_at: float = 0.0


@dataclass(frozen=True, kw_only=True)
class StorageLockdownReleased(Event):
    """A lockdown release transition completed.

    Fires after ``DownloadLockdownService.release()`` cleared the
    state file. Mirrors ``StorageLockdownEngaged``'s shape so a UI
    that reacts to either branch can read a single tuple field.
    """

    EVENT_TYPE: ClassVar[str] = "storage.lockdown_released"

    released_by: str
    released_clients: tuple[str, ...] = ()
    released_at: float = 0.0


@dataclass(frozen=True, kw_only=True)
class StorageCleanupInvoked(Event):
    """A cleanup pass ran (auto or manual) and the strategy reported
    its tally.

    ``deleted`` is the count of torrents the pass actually removed;
    ``freed_bytes`` is the qBittorrent-reported aggregate size; ``kept``
    counts torrents that matched the candidate filter but were trimmed
    by ``max_delete_per_run``. ``strategy`` echoes the chosen ordering
    (``"oldest_first"`` / ``"largest_first"`` / ``"poor_ratio_first"``
    / ``"watched_first"``). ``force`` is True when the manual ``Run
    cleanup now`` path bypassed the threshold check.
    """

    EVENT_TYPE: ClassVar[str] = "storage.cleanup_invoked"

    deleted: int
    freed_bytes: int = 0
    kept: int = 0
    strategy: str = "oldest_first"
    force: bool = False


__all__ = [
    "StorageCleanupInvoked",
    "StorageLockdownEngaged",
    "StorageLockdownReleased",
]
