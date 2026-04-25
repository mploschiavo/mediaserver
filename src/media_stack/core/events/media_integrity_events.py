"""Typed domain events for the media-integrity subsystem.

These events let the UI, metrics, and notification dispatcher react
to the reconciler + enforcer without reaching into their internals.
The Security / Media-Integrity tab subscribes to
``MediaIntegrityDuplicateReviewNeeded`` so operators see the rare
indecisive cases as a calm "needs review" chip — the normal
successful-resolution path stays silent on the UI and only lands
in the audit log and Prometheus metrics.

Events are frozen + kw_only like the rest of the bus contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from media_stack.core.events.bus import Event


@dataclass(frozen=True, kw_only=True)
class MediaIntegrityConfigEnforced(Event):
    """A Servarr/Bazarr adapter's media-management + naming config
    was brought into compliance with the canonical policy.

    ``fields_changed`` lists the per-app field names that actually
    differed from policy (not every key the policy mentions). If
    ``fields_changed`` is empty, the adapter was already compliant
    and the event still fires so the UI's "last verified at"
    timestamp updates.
    """

    EVENT_TYPE: ClassVar[str] = "media_integrity.config_enforced"

    app: str  # "radarr" | "sonarr" | "lidarr" | "readarr" | "bazarr"
    fields_changed: tuple[str, ...] = ()
    sections_applied: tuple[str, ...] = ()  # "mediamanagement", "naming"


@dataclass(frozen=True, kw_only=True)
class MediaIntegrityConfigEnforceFailed(Event):
    """An adapter refused the enforced config (HTTP error, timeout,
    unexpected shape). The enforcer moves on to the next adapter;
    this event signals the failure to operators.

    Downstream: notification dispatcher pages/emails on repeated
    failures for the same app; single transient failures are
    absorbed."""

    EVENT_TYPE: ClassVar[str] = "media_integrity.config_enforce_failed"

    app: str
    section: str  # "mediamanagement" | "naming"
    error: str  # redacted — no API keys/URLs with secrets


@dataclass(frozen=True, kw_only=True)
class MediaIntegrityDuplicateResolved(Event):
    """The reconciler detected duplicate files for a release and
    successfully deleted the loser(s).

    Silent success — UI does not surface this; the user never knows
    it happened. Audit log + Prometheus counter + the Security-tab
    "last reconciled" timestamp are the only observability hooks.
    """

    EVENT_TYPE: ClassVar[str] = "media_integrity.duplicate_resolved"

    app: str
    release_id: str
    release_title: str
    winner_file_id: str
    loser_file_ids: tuple[str, ...]
    total_bytes_freed: int = 0


@dataclass(frozen=True, kw_only=True)
class MediaIntegrityDuplicateReviewNeeded(Event):
    """The reconciler found duplicates but couldn't pick a winner
    deterministically (tied quality score AND tied timestamp AND
    tied file size — genuinely ambiguous).

    This is the ONLY case the UI surfaces. Shows as a calm "needs
    review" chip on the Security tab, not a modal or toast. Operator
    clicks through, sees the two files side-by-side, picks one."""

    EVENT_TYPE: ClassVar[str] = "media_integrity.duplicate_review_needed"

    app: str
    release_id: str
    release_title: str
    candidate_file_ids: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class MediaIntegrityReconcileFailed(Event):
    """The reconciler couldn't complete a pass against an adapter
    (HTTP failure mid-walk, delete_file returned 5xx, etc.). The
    reconciler continues to the next adapter; this event records
    the failure for the audit log and metrics."""

    EVENT_TYPE: ClassVar[str] = "media_integrity.reconcile_failed"

    app: str
    release_id: str  # "" if failure was at the list_releases step
    error: str
