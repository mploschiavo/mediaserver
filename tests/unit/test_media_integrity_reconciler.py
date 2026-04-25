"""Tests for ``MediaIntegrityReconciler`` (Servarr-family).

Verifies winner-picking, audit + event emission, failure handling,
and the "needs review" path."""

from __future__ import annotations

from typing import Any

import pytest

from media_stack.core.auth.users.audit_actions import (
    MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
    MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED,
    MEDIA_INTEGRITY_RECONCILE_FAILED,
)
from media_stack.core.events import (
    MediaIntegrityDuplicateResolved,
    MediaIntegrityDuplicateReviewNeeded,
    MediaIntegrityReconcileFailed,
)
from media_stack.services.media_integrity.arr_protocol import (
    AdapterCapabilities,
    MediaFile,
    MediaRelease,
    QualityProfile,
)
from media_stack.services.media_integrity.reconciler import (
    DuplicateResolution,
    MediaIntegrityReconciler,
    PendingReview,
    _pick_winner,
)


def _file(
    *,
    id: str,
    quality_score: int = 0,
    added_at: str = "",
    size: int = 0,
    release_id: str = "1",
    quality_name: str = "",
) -> MediaFile:
    return MediaFile(
        id=id,
        release_id=release_id,
        relative_path=f"{id}.mkv",
        absolute_path=f"/media/{id}.mkv",
        size=size,
        quality_name=quality_name,
        quality_score=quality_score,
        added_at=added_at,
    )


class _FakeAdapter:
    name = "radarr"
    api_version = "v3"
    media_root = "/media"
    capabilities = AdapterCapabilities()

    def __init__(
        self,
        *,
        releases: list[MediaRelease] | None = None,
        files_by_release: dict[str, list[MediaFile]] | None = None,
        list_releases_raises: Exception | None = None,
        list_files_raises: dict[str, Exception] | None = None,
        delete_raises: dict[str, Exception] | None = None,
        profiles: list[QualityProfile] | None = None,
        file_to_releases: dict[str, list[str]] | None = None,
        list_releases_for_file_raises: dict[str, Exception] | None = None,
    ) -> None:
        self._releases = list(releases or [])
        self._files = dict(files_by_release or {})
        self._list_releases_raises = list_releases_raises
        self._list_files_raises = dict(list_files_raises or {})
        self._delete_raises = dict(delete_raises or {})
        self._profiles = list(profiles or [])
        self._file_to_releases = dict(file_to_releases or {})
        self._list_releases_for_file_raises = dict(
            list_releases_for_file_raises or {}
        )
        self.deleted: list[str] = []

    def list_releases(self) -> list[MediaRelease]:
        if self._list_releases_raises:
            raise self._list_releases_raises
        return list(self._releases)

    def list_files_for(self, release_id: str) -> list[MediaFile]:
        if release_id in self._list_files_raises:
            raise self._list_files_raises[release_id]
        return list(self._files.get(release_id, []))

    def delete_file(self, file_id: str) -> None:
        if file_id in self._delete_raises:
            raise self._delete_raises[file_id]
        self.deleted.append(file_id)

    def quality_score(self, file: MediaFile) -> int:
        return file.quality_score

    def list_releases_for_file(self, file_id: str) -> list[str]:
        if file_id in self._list_releases_for_file_raises:
            raise self._list_releases_for_file_raises[file_id]
        if file_id in self._file_to_releases:
            return list(self._file_to_releases[file_id])
        # 1:1 default — find the release that owns this file_id.
        for rid, files in self._files.items():
            if any(f.id == file_id for f in files):
                return [rid]
        return []

    # protocol completeness — unused
    def get_media_management(self) -> dict[str, Any]:
        return {}

    def put_media_management(self, cfg: dict[str, Any]) -> None:
        pass

    def get_naming(self) -> dict[str, Any]:
        return {}

    def put_naming(self, cfg: dict[str, Any]) -> None:
        pass

    def quality_profiles(self) -> list[QualityProfile]:
        return list(self._profiles)

    def media_management_field_map(self) -> dict[str, str]:
        return {}

    def naming_field_map(self) -> dict[str, str]:
        return {}


class _AuditSpy:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def append(self, **kwargs: Any) -> dict[str, Any]:
        self.entries.append(kwargs)
        return kwargs


class _BusSpy:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Pure winner-picker
# ---------------------------------------------------------------------------


def test_pick_winner_picks_highest_quality() -> None:
    a = _file(id="a", quality_score=5, added_at="2026-04-01")
    b = _file(id="b", quality_score=10, added_at="2026-04-15")
    adapter = _FakeAdapter()
    winner, losers = _pick_winner([a, b], adapter)
    assert winner is b
    assert losers == [a]


def test_pick_winner_ties_break_by_earliest_added() -> None:
    a = _file(id="a", quality_score=5, added_at="2026-04-01", size=200)
    b = _file(id="b", quality_score=5, added_at="2026-04-10", size=100)
    adapter = _FakeAdapter()
    winner, losers = _pick_winner([a, b], adapter)
    assert winner is a  # earlier wins despite larger size
    assert losers == [b]


def test_pick_winner_ties_break_by_smallest_size() -> None:
    a = _file(id="a", quality_score=5, added_at="2026-04-01", size=200)
    b = _file(id="b", quality_score=5, added_at="2026-04-01", size=100)
    adapter = _FakeAdapter()
    winner, losers = _pick_winner([a, b], adapter)
    assert winner is b
    assert losers == [a]


def test_pick_winner_total_tie_returns_none() -> None:
    a = _file(id="a", quality_score=5, added_at="2026-04-01", size=100)
    b = _file(id="b", quality_score=5, added_at="2026-04-01", size=100)
    adapter = _FakeAdapter()
    winner, losers = _pick_winner([a, b], adapter)
    assert winner is None
    assert losers == []


def test_pick_winner_single_file_returns_itself() -> None:
    a = _file(id="a")
    adapter = _FakeAdapter()
    winner, losers = _pick_winner([a], adapter)
    assert winner is a
    assert losers == []


def test_pick_winner_empty_list() -> None:
    adapter = _FakeAdapter()
    winner, losers = _pick_winner([], adapter)
    assert winner is None
    assert losers == []


# ---------------------------------------------------------------------------
# Reconciler integration
# ---------------------------------------------------------------------------


def test_reconciler_resolves_a_simple_duplicate() -> None:
    release = MediaRelease(
        id="42",
        title="Spider-Man: No Way Home",
        path="/media/movies/Spider-Man",
    )
    keep = _file(id="keep", quality_score=10, added_at="2026-04-01", size=10_000_000_000, release_id="42")
    drop = _file(id="drop", quality_score=5, added_at="2026-04-10", size=8_000_000_000, release_id="42")
    adapter = _FakeAdapter(
        releases=[release], files_by_release={"42": [keep, drop]},
    )
    audit = _AuditSpy()
    bus = _BusSpy()
    rec = MediaIntegrityReconciler(audit=audit, event_bus=bus)
    report = rec.reconcile([adapter])

    assert report.total_resolved == 1
    assert adapter.deleted == ["drop"]
    assert report.total_bytes_freed == 8_000_000_000

    resolved = [e for e in bus.events if isinstance(e, MediaIntegrityDuplicateResolved)]
    assert len(resolved) == 1
    assert resolved[0].winner_file_id == "keep"
    assert resolved[0].loser_file_ids == ("drop",)

    success_audits = [
        e for e in audit.entries if e["action"] == MEDIA_INTEGRITY_DUPLICATE_RESOLVED
    ]
    assert len(success_audits) == 1


def test_reconciler_skips_when_only_one_file() -> None:
    release = MediaRelease(id="1", title="Solo", path="/")
    only = _file(id="only", quality_score=5, release_id="1")
    adapter = _FakeAdapter(releases=[release], files_by_release={"1": [only]})
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    assert report.total_resolved == 0
    assert adapter.deleted == []


def test_reconciler_skips_when_no_releases() -> None:
    adapter = _FakeAdapter(releases=[], files_by_release={})
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    assert report.results[0].resolved == ()


def test_reconciler_emits_review_on_total_tie() -> None:
    release = MediaRelease(id="42", title="Dune", path="/")
    a = _file(id="a", quality_score=5, added_at="2026-04-01", size=100, release_id="42")
    b = _file(id="b", quality_score=5, added_at="2026-04-01", size=100, release_id="42")
    adapter = _FakeAdapter(releases=[release], files_by_release={"42": [a, b]})
    audit = _AuditSpy()
    bus = _BusSpy()
    rec = MediaIntegrityReconciler(audit=audit, event_bus=bus)
    report = rec.reconcile([adapter])

    assert report.total_needs_review == 1
    assert adapter.deleted == []
    review = report.results[0].needs_review[0]
    assert sorted(review.candidate_file_ids) == ["a", "b"]

    bus_review = [
        e for e in bus.events if isinstance(e, MediaIntegrityDuplicateReviewNeeded)
    ]
    assert len(bus_review) == 1
    audit_review = [
        e for e in audit.entries
        if e["action"] == MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED
    ]
    assert len(audit_review) == 1


def test_reconciler_handles_list_releases_failure() -> None:
    adapter = _FakeAdapter(list_releases_raises=RuntimeError("connection refused"))
    audit = _AuditSpy()
    bus = _BusSpy()
    rec = MediaIntegrityReconciler(audit=audit, event_bus=bus)
    report = rec.reconcile([adapter])
    assert report.total_failures >= 1
    assert any(
        isinstance(e, MediaIntegrityReconcileFailed) for e in bus.events
    )
    assert any(
        e["action"] == MEDIA_INTEGRITY_RECONCILE_FAILED for e in audit.entries
    )


def test_reconciler_handles_list_files_failure_for_one_release() -> None:
    r1 = MediaRelease(id="ok", title="Working", path="/")
    r2 = MediaRelease(id="broken", title="Broken", path="/")
    keep = _file(id="keep", quality_score=10, release_id="ok", added_at="2026-04-01")
    drop = _file(id="drop", quality_score=5, release_id="ok", added_at="2026-04-10")
    adapter = _FakeAdapter(
        releases=[r1, r2],
        files_by_release={"ok": [keep, drop]},
        list_files_raises={"broken": RuntimeError("500")},
    )
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    # ok release was still healed
    assert adapter.deleted == ["drop"]
    # broken release was reported as a failure
    assert report.total_failures == 1


def test_reconciler_marks_review_when_every_delete_fails() -> None:
    release = MediaRelease(id="1", title="X", path="/")
    keep = _file(id="keep", quality_score=10, release_id="1", added_at="2026-04-01")
    drop = _file(id="drop", quality_score=5, release_id="1", added_at="2026-04-10")
    adapter = _FakeAdapter(
        releases=[release],
        files_by_release={"1": [keep, drop]},
        delete_raises={"drop": RuntimeError("403")},
    )
    bus = _BusSpy()
    rec = MediaIntegrityReconciler(event_bus=bus)
    report = rec.reconcile([adapter])
    # Delete failed → needs review surfaces
    assert report.total_needs_review == 1
    assert any(
        isinstance(e, MediaIntegrityDuplicateReviewNeeded) for e in bus.events
    )


def test_reconciler_partial_delete_success() -> None:
    """If 1 of 2 losers' deletes succeeds, we should record the
    successful one as resolved (bytes freed > 0, no review)."""
    release = MediaRelease(id="1", title="X", path="/")
    keep = _file(id="keep", quality_score=10, release_id="1", added_at="2026-04-01", size=1)
    drop1 = _file(id="drop1", quality_score=5, release_id="1", added_at="2026-04-10", size=100)
    drop2 = _file(id="drop2", quality_score=5, release_id="1", added_at="2026-04-11", size=200)
    adapter = _FakeAdapter(
        releases=[release],
        files_by_release={"1": [keep, drop1, drop2]},
        delete_raises={"drop2": RuntimeError("403")},
    )
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    assert report.total_resolved == 1
    assert adapter.deleted == ["drop1"]
    assert report.total_bytes_freed == 100


def test_reconciler_works_without_audit_or_bus() -> None:
    release = MediaRelease(id="1", title="X", path="/")
    a = _file(id="a", quality_score=10, release_id="1", added_at="2026-04-01")
    b = _file(id="b", quality_score=5, release_id="1", added_at="2026-04-10")
    adapter = _FakeAdapter(releases=[release], files_by_release={"1": [a, b]})
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    assert report.total_resolved == 1


# ---------------------------------------------------------------------------
# Task 1 — Dry-run mode
# ---------------------------------------------------------------------------


def test_reconciler_dry_run_does_not_delete() -> None:
    release = MediaRelease(id="1", title="X", path="/")
    keep = _file(id="keep", quality_score=10, release_id="1", added_at="2026-04-01", size=10)
    drop = _file(id="drop", quality_score=5, release_id="1", added_at="2026-04-10", size=200)
    adapter = _FakeAdapter(releases=[release], files_by_release={"1": [keep, drop]})
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter], dry_run=True)
    assert adapter.deleted == []
    assert report.dry_run is True
    assert report.total_resolved == 1
    assert report.total_bytes_freed == 200
    resolution = report.results[0].resolved[0]
    assert resolution.winner_file_id == "keep"
    assert resolution.loser_file_ids == ("drop",)
    assert resolution.bytes_freed == 200


def test_reconciler_dry_run_no_audit_on_deletions() -> None:
    release = MediaRelease(id="1", title="X", path="/")
    keep = _file(id="keep", quality_score=10, release_id="1", added_at="2026-04-01")
    drop = _file(id="drop", quality_score=5, release_id="1", added_at="2026-04-10")
    adapter = _FakeAdapter(releases=[release], files_by_release={"1": [keep, drop]})
    audit = _AuditSpy()
    bus = _BusSpy()
    rec = MediaIntegrityReconciler(audit=audit, event_bus=bus)
    rec.reconcile([adapter], dry_run=True)
    resolved_audits = [
        e for e in audit.entries if e["action"] == MEDIA_INTEGRITY_DUPLICATE_RESOLVED
    ]
    assert resolved_audits == []
    resolved_events = [
        e for e in bus.events if isinstance(e, MediaIntegrityDuplicateResolved)
    ]
    assert resolved_events == []


def test_reconciler_dry_run_still_emits_review_events() -> None:
    """Dry-run suppresses delete events — but a needs-review case isn't a
    deletion, it's a read-only signal, so it MUST still surface."""
    release = MediaRelease(id="1", title="X", path="/")
    a = _file(id="a", quality_score=5, release_id="1", added_at="2026-04-01", size=100)
    b = _file(id="b", quality_score=5, release_id="1", added_at="2026-04-01", size=100)
    adapter = _FakeAdapter(releases=[release], files_by_release={"1": [a, b]})
    audit = _AuditSpy()
    bus = _BusSpy()
    rec = MediaIntegrityReconciler(audit=audit, event_bus=bus)
    rec.reconcile([adapter], dry_run=True)
    assert any(
        isinstance(e, MediaIntegrityDuplicateReviewNeeded) for e in bus.events
    )
    assert any(
        e["action"] == MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED for e in audit.entries
    )


# ---------------------------------------------------------------------------
# Task 2 — Profile-item ordering
# ---------------------------------------------------------------------------


def test_reconciler_respects_profile_item_order() -> None:
    """Profile order: HDTV-720p (rank 0) > Bluray-1080p (rank 2). The
    HDTV file should win even though raw quality_score would prefer
    1080p."""
    profile = QualityProfile(
        id=4,
        name="Custom",
        cutoff_id=0,
        items=(
            {"quality": {"id": 1, "name": "HDTV-720p"}, "allowed": True},
            {"quality": {"id": 5, "name": "WEBDL-1080p"}, "allowed": True},
            {"quality": {"id": 7, "name": "Bluray-1080p"}, "allowed": True},
        ),
    )
    release = MediaRelease(
        id="1", title="X", path="/", quality_profile_id=4
    )
    hd = _file(
        id="hd",
        quality_score=1,
        quality_name="HDTV-720p",
        release_id="1",
        added_at="2026-04-10",
    )
    bd = _file(
        id="bd",
        quality_score=7,
        quality_name="Bluray-1080p",
        release_id="1",
        added_at="2026-04-01",
    )
    adapter = _FakeAdapter(
        releases=[release],
        files_by_release={"1": [hd, bd]},
        profiles=[profile],
    )
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    assert report.total_resolved == 1
    assert adapter.deleted == ["bd"]


def test_reconciler_falls_back_to_quality_score_when_profile_missing() -> None:
    """release.quality_profile_id is None — adapter.quality_score wins."""
    release = MediaRelease(id="1", title="X", path="/", quality_profile_id=None)
    hi = _file(
        id="hi",
        quality_score=10,
        quality_name="Bluray-2160p",
        release_id="1",
        added_at="2026-04-10",
    )
    lo = _file(
        id="lo",
        quality_score=2,
        quality_name="HDTV-720p",
        release_id="1",
        added_at="2026-04-01",
    )
    adapter = _FakeAdapter(
        releases=[release], files_by_release={"1": [hi, lo]}, profiles=[]
    )
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    assert report.total_resolved == 1
    assert adapter.deleted == ["lo"]


def test_reconciler_handles_nested_profile_groups() -> None:
    """A profile group's nested ``items`` should be flat-walked in
    order. Group ordering preserved across the boundary."""
    profile = QualityProfile(
        id=4,
        name="Grouped",
        cutoff_id=0,
        items=(
            {
                "name": "WEB",
                "allowed": True,
                "items": [
                    {"quality": {"id": 5, "name": "WEBDL-1080p"}, "allowed": True},
                    {"quality": {"id": 6, "name": "WEBDL-2160p"}, "allowed": True},
                ],
            },
            {"quality": {"id": 7, "name": "Bluray-1080p"}, "allowed": True},
        ),
    )
    release = MediaRelease(
        id="1", title="X", path="/", quality_profile_id=4
    )
    # Bluray-1080p is rank 2, WEBDL-1080p is rank 0 → WEBDL wins.
    web = _file(
        id="web",
        quality_score=5,
        quality_name="WEBDL-1080p",
        release_id="1",
        added_at="2026-04-10",
    )
    bd = _file(
        id="bd",
        quality_score=7,
        quality_name="Bluray-1080p",
        release_id="1",
        added_at="2026-04-01",
    )
    adapter = _FakeAdapter(
        releases=[release],
        files_by_release={"1": [web, bd]},
        profiles=[profile],
    )
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    assert adapter.deleted == ["bd"]
    assert report.total_resolved == 1


def test_reconciler_handles_disallowed_items() -> None:
    """A file whose ``quality_name`` matches a disallowed profile
    item gets the unknown rank (sorts to the end)."""
    profile = QualityProfile(
        id=4,
        name="Filtered",
        cutoff_id=0,
        items=(
            {"quality": {"id": 1, "name": "HDTV-720p"}, "allowed": True},
            {"quality": {"id": 5, "name": "WEBDL-1080p"}, "allowed": False},
        ),
    )
    release = MediaRelease(
        id="1", title="X", path="/", quality_profile_id=4
    )
    hd = _file(
        id="hd",
        quality_score=1,
        quality_name="HDTV-720p",
        release_id="1",
        added_at="2026-04-10",
    )
    web = _file(
        id="web",
        quality_score=5,
        quality_name="WEBDL-1080p",
        release_id="1",
        added_at="2026-04-01",
    )
    adapter = _FakeAdapter(
        releases=[release],
        files_by_release={"1": [hd, web]},
        profiles=[profile],
    )
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    # HDTV-720p (rank 0) wins; WEBDL-1080p was disallowed so its
    # rank is "unknown" (last).
    assert adapter.deleted == ["web"]
    assert report.total_resolved == 1


def test_reconciler_caches_profiles_once_per_pass() -> None:
    """Profiles are fetched ONCE per adapter, not once per release."""
    call_count = {"n": 0}

    profile = QualityProfile(id=4, name="P", cutoff_id=0, items=())
    r1 = MediaRelease(id="r1", title="A", path="/", quality_profile_id=4)
    r2 = MediaRelease(id="r2", title="B", path="/", quality_profile_id=4)
    a = _file(id="a", quality_score=1, release_id="r1", added_at="2026-04-01")
    b = _file(id="b", quality_score=2, release_id="r1", added_at="2026-04-02")
    c = _file(id="c", quality_score=1, release_id="r2", added_at="2026-04-01")
    d = _file(id="d", quality_score=2, release_id="r2", added_at="2026-04-02")
    adapter = _FakeAdapter(
        releases=[r1, r2],
        files_by_release={"r1": [a, b], "r2": [c, d]},
    )

    def counting_profiles() -> list[QualityProfile]:
        call_count["n"] += 1
        return [profile]

    adapter.quality_profiles = counting_profiles  # type: ignore[method-assign]

    rec = MediaIntegrityReconciler()
    rec.reconcile([adapter])
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# Task 3 — Shared-file safety
# ---------------------------------------------------------------------------


def test_list_releases_for_file_default_returns_owning_release() -> None:
    """The fake's default 1:1 lookup returns ``[release_id]``."""
    release = MediaRelease(id="1", title="X", path="/")
    only = _file(id="only", release_id="1")
    adapter = _FakeAdapter(releases=[release], files_by_release={"1": [only]})
    assert adapter.list_releases_for_file("only") == ["1"]


def test_reconciler_refuses_to_delete_shared_file() -> None:
    """Loser file backs ep1 AND ep2; reconciling ep1 must not delete it."""
    ep1 = MediaRelease(id="1001", title="The Bear S01E01", path="/")
    ep2 = MediaRelease(id="1002", title="The Bear S01E02", path="/")
    keep = _file(
        id="keep", quality_score=10, release_id="1001", added_at="2026-04-01"
    )
    shared = _file(
        id="shared", quality_score=5, release_id="1001", added_at="2026-04-10",
    )
    adapter = _FakeAdapter(
        releases=[ep1, ep2],
        files_by_release={"1001": [keep, shared], "1002": []},
        # Shared backs both episodes — explicit override.
        file_to_releases={"shared": ["1001", "1002"], "keep": ["1001"]},
    )
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    assert "shared" not in adapter.deleted
    # All losers were skipped → falls through to needs-review.
    assert report.total_needs_review == 1


def test_reconciler_deletes_when_file_linked_only_to_current_release() -> None:
    """Single-episode case: the loser is bound to ONE release; delete normally."""
    release = MediaRelease(id="1001", title="Single Ep", path="/")
    keep = _file(id="keep", quality_score=10, release_id="1001", added_at="2026-04-01")
    drop = _file(id="drop", quality_score=5, release_id="1001", added_at="2026-04-10")
    adapter = _FakeAdapter(
        releases=[release],
        files_by_release={"1001": [keep, drop]},
        file_to_releases={"keep": ["1001"], "drop": ["1001"]},
    )
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    assert adapter.deleted == ["drop"]
    assert report.total_resolved == 1


def test_reconciler_refuses_when_list_releases_for_file_errors() -> None:
    """If linkage lookup raises, treat as unknown → do NOT delete."""
    release = MediaRelease(id="1", title="X", path="/")
    keep = _file(id="keep", quality_score=10, release_id="1", added_at="2026-04-01")
    drop = _file(id="drop", quality_score=5, release_id="1", added_at="2026-04-10")
    adapter = _FakeAdapter(
        releases=[release],
        files_by_release={"1": [keep, drop]},
        list_releases_for_file_raises={"drop": RuntimeError("500")},
    )
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter])
    assert adapter.deleted == []
    assert report.total_needs_review == 1


def test_reconciler_dry_run_skips_delete_for_shared_file() -> None:
    """Dry-run + shared-file: still skipped, still surfaces as review."""
    ep1 = MediaRelease(id="1", title="E1", path="/")
    ep2 = MediaRelease(id="2", title="E2", path="/")
    keep = _file(id="keep", quality_score=10, release_id="1", added_at="2026-04-01")
    shared = _file(id="shared", quality_score=5, release_id="1", added_at="2026-04-10")
    adapter = _FakeAdapter(
        releases=[ep1, ep2],
        files_by_release={"1": [keep, shared], "2": []},
        file_to_releases={"shared": ["1", "2"], "keep": ["1"]},
    )
    rec = MediaIntegrityReconciler()
    report = rec.reconcile([adapter], dry_run=True)
    assert adapter.deleted == []
    assert report.total_needs_review == 1
    assert report.dry_run is True


# ---------------------------------------------------------------------------
# Profile-flatten helpers (exercised through _pick_winner already, but
# pin behavior explicitly so a profile-shape regression has a sharp test)
# ---------------------------------------------------------------------------


def test_pick_winner_with_profile_order_uses_rank_not_score() -> None:
    """Even though file ``a`` has lower raw quality_score, its
    quality_name has lower (better) profile rank → it wins."""
    a = _file(
        id="a",
        quality_score=2,
        quality_name="HDTV-720p",
        added_at="2026-04-10",
    )
    b = _file(
        id="b",
        quality_score=10,
        quality_name="Bluray-1080p",
        added_at="2026-04-01",
    )
    adapter = _FakeAdapter()
    profile_order = {"HDTV-720p": 0, "Bluray-1080p": 2}
    winner, losers = _pick_winner(
        [a, b], adapter, profile_order=profile_order
    )
    assert winner is a
    assert losers == [b]


def test_pick_winner_unknown_quality_name_loses() -> None:
    a = _file(id="a", quality_name="Bluray-1080p", added_at="2026-04-10")
    b = _file(id="b", quality_name="Mystery-Format", added_at="2026-04-01")
    adapter = _FakeAdapter()
    profile_order = {"Bluray-1080p": 0}
    winner, _ = _pick_winner([a, b], adapter, profile_order=profile_order)
    assert winner is a
