"""Reconcile duplicate files across every Servarr adapter.

The reconciler walks each adapter's releases, groups files per
release, and heals duplicates by deleting losers. It is the steady-
state guardrail; the enforcer is the config guardrail. Together
they mean a non-technical user never has to learn the word
"duplicate".

Winner-picking policy
---------------------
When a release has >= 2 files the reconciler picks ONE to keep by
these rules, in order:

1. **Highest quality score first.** That's the adapter's own
   ``quality_score(file)``, which resolves to the Servarr quality
   profile ordering (e.g., WEBDL-2160p > WEBDL-1080p > HDTV-1080p).
2. **Earliest ``added_at`` wins ties.** A tie on quality score is
   usually two variants of the same tier. Keeping the earlier one
   is stable - the later import was the surprise.
3. **Smallest ``size`` wins remaining ties.** If quality + time are
   genuinely identical, a smaller file is likely a clean mux and a
   larger file is likely a bloated re-encode; we bias toward the
   smaller one. This is a rare branch.

If rule 3 still can't pick, emit
``MediaIntegrityDuplicateReviewNeeded`` and leave both files in
place. That's the ONLY case the UI surfaces - a calm "needs
review" chip, not a panic alert.

Hardlink safety
---------------
``use_hardlinks: true`` in the policy means the torrent file and
the library file share an inode. Deleting the library copy does
NOT delete the torrent's copy; the filesystem reference count
drops by one. That's what makes this reconciler safe under the
canonical policy - we're never destroying a user's torrent.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable

from media_stack.core.auth.users.audit_actions import (
    MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
    MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED,
    MEDIA_INTEGRITY_RECONCILE_FAILED,
)
from media_stack.core.events import (
    EventBus,
    MediaIntegrityDuplicateResolved,
    MediaIntegrityDuplicateReviewNeeded,
    MediaIntegrityReconcileFailed,
)
from media_stack.domain.media_integrity.arr_protocol import (
    ArrApp,
    MediaFile,
    MediaRelease,
    QualityProfile,
)


# Sentinel for "this file's quality_name is not in the profile". The
# winner-picker treats it as the worst possible rank - unknown
# qualities sort to the bottom so a known-good file always beats them.
_UNKNOWN_PROFILE_RANK = 1 << 30


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DuplicateResolution:
    release_id: str
    release_title: str
    winner_file_id: str
    loser_file_ids: tuple[str, ...]
    bytes_freed: int


@dataclass(frozen=True)
class PendingReview:
    release_id: str
    release_title: str
    candidate_file_ids: tuple[str, ...]


@dataclass(frozen=True)
class AdapterReconcileResult:
    app: str
    resolved: tuple[DuplicateResolution, ...] = ()
    needs_review: tuple[PendingReview, ...] = ()
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconcileReport:
    results: tuple[AdapterReconcileResult, ...] = ()
    total_resolved: int = 0
    total_needs_review: int = 0
    total_failures: int = 0
    total_bytes_freed: int = 0
    dry_run: bool = False


class _AuditSink:

    def append(
        self,
        actor: str,
        action: str,
        target: str,
        result: str = "ok",
        ip: str = "",
        user_agent: str = "",
        detail: dict[str, Any] | None = None,
    ) -> Any:
        ...  # pragma: no cover - structural


class _WinnerPicker:
    """Pure winner-picking helpers (no side effects, easy to unit-test).

    Encapsulates the rules described in the module docstring. Held as
    a stateless instance and dispatched through ``sys.modules[__name__]``
    aliases so tests can ``mock.patch`` the module-level names.
    """

    def pick_winner(
        self,
        files: Iterable[MediaFile],
        adapter: ArrApp,
        *,
        profile_order: dict[str, int] | None = None,
    ) -> tuple[MediaFile | None, list[MediaFile]]:
        """Apply the winner-picking rules.

        Returns ``(winner, losers)`` where ``losers`` is every other
        file. If the rules can't decide (total tie), ``winner`` is
        ``None`` - the reconciler then emits a review-needed event.

        When ``profile_order`` is supplied (and non-empty), it overrides
        the raw ``quality_score`` for the primary tiebreak: the index in
        the profile's ordered ``items`` list determines preference (lower
        index = preferred). Files whose ``quality_name`` doesn't appear
        in the map sort to the back. Without a profile, we fall back to
        the historical ``adapter.quality_score(file)`` ordering.
        """
        files = list(files)
        if not files:
            return None, []
        if len(files) == 1:
            return files[0], []

        use_profile = bool(profile_order)
        ordered = sorted(
            files,
            key=lambda f: self._sort_key(
                f, adapter, profile_order=profile_order, use_profile=use_profile
            ),
        )
        top = ordered[0]
        runner_up = ordered[1]
        if self.keys_equal(top, runner_up, adapter, profile_order=profile_order):
            # Genuine tie across all 3 rules - needs human review.
            return None, []
        return top, ordered[1:]

    def _sort_key(
        self,
        f: MediaFile,
        adapter: ArrApp,
        *,
        profile_order: dict[str, int] | None,
        use_profile: bool,
    ) -> tuple[int, str, int]:
        if use_profile:
            assert profile_order is not None
            rank = profile_order.get(f.quality_name, _UNKNOWN_PROFILE_RANK)
            # Lower rank = better; sort ascending.
            return (rank, f.added_at, f.size)
        # We want the "winner" to have the LARGEST quality_score
        # (highest tier), the EARLIEST added_at, and the SMALLEST
        # size. Python sorts ascending, so negate score.
        return (-adapter.quality_score(f), f.added_at, f.size)

    def keys_equal(
        self,
        a: MediaFile,
        b: MediaFile,
        adapter: ArrApp,
        *,
        profile_order: dict[str, int] | None = None,
    ) -> bool:
        if profile_order:
            rank_a = profile_order.get(a.quality_name, _UNKNOWN_PROFILE_RANK)
            rank_b = profile_order.get(b.quality_name, _UNKNOWN_PROFILE_RANK)
            primary_equal = rank_a == rank_b
        else:
            primary_equal = adapter.quality_score(a) == adapter.quality_score(b)
        return primary_equal and a.added_at == b.added_at and a.size == b.size

    def profile_order_for(
        self,
        release: MediaRelease,
        profiles_by_id: dict[int, QualityProfile],
    ) -> dict[str, int]:
        """Build ``{quality_name: rank}`` for the release's profile.

        Returns an empty dict (= "use raw quality_score fallback") when
        the release has no ``quality_profile_id`` or the id isn't in the
        cached profile list."""
        pid = release.quality_profile_id
        if pid is None:
            return {}
        profile = profiles_by_id.get(pid)
        if profile is None:
            return {}
        return self.flatten_profile_items(profile.items)

    def flatten_profile_items(
        self,
        items: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ) -> dict[str, int]:
        """Walk a Servarr profile's items list, including nested groups,
        and return ``{quality_name: index}`` where lower index is preferred.

        Servarr profile shapes seen across versions:
          - leaf:  ``{"quality": {"id": 7, "name": "WEBDL-1080p"}, "allowed": true}``
          - group: ``{"name": "WEBDL", "items": [{leaf...}, ...], "allowed": true}``

        Items with ``allowed=false`` are skipped - disallowed qualities
        can't be a winner. The flat-walk index is the rank."""
        out: dict[str, int] = {}
        counter = [0]
        self._walk_profile_items(items, out, counter)
        return out

    def _walk_profile_items(
        self,
        seq: Iterable[Any],
        out: dict[str, int],
        counter: list[int],
    ) -> None:
        for item in seq:
            if not isinstance(item, dict):
                continue
            if item.get("allowed", True) is False:
                continue
            nested = item.get("items")
            if isinstance(nested, list) and nested:
                self._walk_profile_items(nested, out, counter)
                continue
            quality = item.get("quality")
            if isinstance(quality, dict):
                name = quality.get("name")
                if isinstance(name, str) and name and name not in out:
                    out[name] = counter[0]
                    counter[0] += 1


class _ErrorRedactor:
    """Strips secrets from exception text before audit/log surfaces."""

    _APIKEY_RE = re.compile(r"(?i)(apikey|api_key|x-api-key)\s*[=:]\s*\S+")
    _HEX_RE = re.compile(r"[a-f0-9]{32,}")

    def redact(self, text: str) -> str:
        if not text:
            return ""
        redacted = self._APIKEY_RE.sub(r"\1=REDACTED", text)
        redacted = self._HEX_RE.sub("REDACTED", redacted)
        return redacted[:500]


# Singletons - instantiated once at import; the helpers are stateless.
_winner_picker = _WinnerPicker()
_error_redactor = _ErrorRedactor()


class MediaIntegrityReconciler:
    """Heals duplicate files across every adapter on each pass.

    Call ``reconcile(adapters)`` per scheduler tick. The method is
    synchronous and self-contained; caller holds no locks on the
    adapters beyond the HTTP client's own.
    """

    def __init__(
        self,
        *,
        audit: _AuditSink | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._audit = audit
        self._bus = event_bus

    def reconcile(
        self,
        adapters: list[ArrApp],
        *,
        actor: str = "system",
        dry_run: bool = False,
    ) -> ReconcileReport:
        results: list[AdapterReconcileResult] = []
        total_resolved = 0
        total_review = 0
        total_failures = 0
        total_bytes = 0
        for adapter in adapters:
            result = self._reconcile_one(adapter, actor=actor, dry_run=dry_run)
            results.append(result)
            total_resolved += len(result.resolved)
            total_review += len(result.needs_review)
            total_failures += len(result.failures)
            total_bytes += sum(r.bytes_freed for r in result.resolved)
        return ReconcileReport(
            results=tuple(results),
            total_resolved=total_resolved,
            total_needs_review=total_review,
            total_failures=total_failures,
            total_bytes_freed=total_bytes,
            dry_run=dry_run,
        )

    # ------------------------------------------------------------------

    def _reconcile_one(
        self, adapter: ArrApp, *, actor: str, dry_run: bool
    ) -> AdapterReconcileResult:
        resolved: list[DuplicateResolution] = []
        needs_review: list[PendingReview] = []
        failures: list[str] = []
        try:
            releases = adapter.list_releases()
        except Exception as exc:
            self._record_failure(adapter, "", exc, failures, actor=actor)
            return AdapterReconcileResult(app=adapter.name, failures=tuple(failures))
        # Cache profiles once per pass - the reconciler may walk
        # thousands of releases and we don't want N HTTP calls.
        profiles_by_id = self._load_profiles(adapter)
        for release in releases:
            try:
                outcome = self._reconcile_release(
                    adapter,
                    release,
                    actor=actor,
                    dry_run=dry_run,
                    profiles_by_id=profiles_by_id,
                )
            except Exception as exc:
                self._record_failure(adapter, release.id, exc, failures, actor=actor)
                continue
            if outcome is None:
                continue
            if isinstance(outcome, DuplicateResolution):
                resolved.append(outcome)
            else:
                needs_review.append(outcome)
        return AdapterReconcileResult(
            app=adapter.name,
            resolved=tuple(resolved),
            needs_review=tuple(needs_review),
            failures=tuple(failures),
        )

    def _load_profiles(self, adapter: ArrApp) -> dict[int, QualityProfile]:
        """Best-effort profile fetch. A missing/erroring profile list
        is recoverable - winner-picking falls back to ``quality_score``."""
        try:
            profiles = adapter.quality_profiles()
        except Exception:
            logger.debug("quality_profiles fetch failed", exc_info=True)
            return {}
        return {p.id: p for p in profiles}

    def _reconcile_release(
        self,
        adapter: ArrApp,
        release: MediaRelease,
        *,
        actor: str,
        dry_run: bool,
        profiles_by_id: dict[int, QualityProfile],
    ) -> DuplicateResolution | PendingReview | None:
        files = adapter.list_files_for(release.id)
        if len(files) < 2:
            return None  # desired state
        # Dispatch through sys.modules[__name__] so tests that
        # ``mock.patch`` the module-level helper aliases see the override.
        _self_module = sys.modules[__name__]
        profile_order = _self_module._profile_order_for(release, profiles_by_id)
        winner, losers = _self_module._pick_winner(
            files, adapter, profile_order=profile_order
        )
        if winner is None:
            return self._emit_needs_review(adapter, release, files, actor=actor)
        bytes_freed = 0
        deleted_ids: list[str] = []
        for loser in losers:
            # Sonarr-safety: a loser file may back OTHER releases too
            # (double-episode files). Refuse to delete in that case.
            try:
                linked = adapter.list_releases_for_file(loser.id)
            except Exception:
                logger.debug(
                    "list_releases_for_file failed; skipping delete defensively",
                    exc_info=True,
                )
                continue
            if not linked or any(rid != release.id for rid in linked):
                # Empty list = linkage unconfirmed; non-empty with others
                # = backs another release. Both cases: don't delete.
                continue
            if dry_run:
                bytes_freed += loser.size
                deleted_ids.append(loser.id)
                continue
            try:
                adapter.delete_file(loser.id)
            except Exception as exc:
                failure_msg = f"delete_file({loser.id}): {_self_module._redact(str(exc))}"
                if self._bus is not None:
                    try:
                        self._bus.publish(
                            MediaIntegrityReconcileFailed(
                                app=adapter.name,
                                release_id=release.id,
                                error=failure_msg,
                            )
                        )
                    except Exception:  # pragma: no cover
                        logger.debug("bus refused reconcile_failed", exc_info=True)
                continue
            bytes_freed += loser.size
            deleted_ids.append(loser.id)
        if not deleted_ids:
            # Every loser was either un-deletable, shared, or skipped.
            # Surface as needs-review so a human picks.
            return self._emit_needs_review(adapter, release, files, actor=actor)
        resolution = DuplicateResolution(
            release_id=release.id,
            release_title=release.title,
            winner_file_id=winner.id,
            loser_file_ids=tuple(deleted_ids),
            bytes_freed=bytes_freed,
        )
        # In dry-run we skip the resolved event/audit so the run looks
        # like an inspection, not a write. Needs-review still emits
        # because that's a read-only signal regardless.
        if not dry_run:
            self._emit_resolved(adapter, resolution, actor=actor)
        return resolution

    # ------------------------------------------------------------------
    # Events / audit
    # ------------------------------------------------------------------

    def _emit_resolved(
        self,
        adapter: ArrApp,
        resolution: DuplicateResolution,
        *,
        actor: str,
    ) -> None:
        if self._bus is not None:
            try:
                self._bus.publish(
                    MediaIntegrityDuplicateResolved(
                        app=adapter.name,
                        release_id=resolution.release_id,
                        release_title=resolution.release_title,
                        winner_file_id=resolution.winner_file_id,
                        loser_file_ids=resolution.loser_file_ids,
                        total_bytes_freed=resolution.bytes_freed,
                    )
                )
            except Exception:  # pragma: no cover
                logger.debug("bus refused duplicate_resolved", exc_info=True)
        if self._audit is not None:
            try:
                self._audit.append(
                    actor=actor,
                    action=MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
                    target=f"{adapter.name}:{resolution.release_id}",
                    detail={
                        "release_title": resolution.release_title,
                        "winner_file_id": resolution.winner_file_id,
                        "loser_file_ids": list(resolution.loser_file_ids),
                        "bytes_freed": resolution.bytes_freed,
                    },
                )
            except Exception:  # pragma: no cover
                logger.debug("audit refused duplicate_resolved", exc_info=True)

    def _emit_needs_review(
        self,
        adapter: ArrApp,
        release: MediaRelease,
        files: list[MediaFile],
        *,
        actor: str,
    ) -> PendingReview:
        candidate_ids = tuple(f.id for f in files)
        review = PendingReview(
            release_id=release.id,
            release_title=release.title,
            candidate_file_ids=candidate_ids,
        )
        if self._bus is not None:
            try:
                self._bus.publish(
                    MediaIntegrityDuplicateReviewNeeded(
                        app=adapter.name,
                        release_id=release.id,
                        release_title=release.title,
                        candidate_file_ids=candidate_ids,
                    )
                )
            except Exception:  # pragma: no cover
                logger.debug("bus refused review_needed", exc_info=True)
        if self._audit is not None:
            try:
                self._audit.append(
                    actor=actor,
                    action=MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED,
                    target=f"{adapter.name}:{release.id}",
                    detail={
                        "release_title": release.title,
                        "candidate_file_ids": list(candidate_ids),
                    },
                )
            except Exception:  # pragma: no cover
                logger.debug("audit refused review_needed", exc_info=True)
        return review

    def _record_failure(
        self,
        adapter: ArrApp,
        release_id: str,
        exc: Exception,
        failures: list[str],
        *,
        actor: str,
    ) -> None:
        error = sys.modules[__name__]._redact(str(exc))
        failures.append(f"{release_id or '*'}: {error}")
        if self._bus is not None:
            try:
                self._bus.publish(
                    MediaIntegrityReconcileFailed(
                        app=adapter.name, release_id=release_id, error=error
                    )
                )
            except Exception:  # pragma: no cover
                logger.debug("bus refused reconcile_failed", exc_info=True)
        if self._audit is not None:
            try:
                self._audit.append(
                    actor=actor,
                    action=MEDIA_INTEGRITY_RECONCILE_FAILED,
                    target=f"{adapter.name}:{release_id}" if release_id else adapter.name,
                    result="failure",
                    detail={"error": error},
                )
            except Exception:  # pragma: no cover
                logger.debug("audit refused reconcile_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Module-level aliases - preserve the legacy import surface and let tests
# ``mock.patch`` the helpers without reaching into the singleton instances.
# Every public name here forwards to the corresponding instance method on
# the module-scope singleton.
# ---------------------------------------------------------------------------

_pick_winner = _winner_picker.pick_winner
_keys_equal = _winner_picker.keys_equal
_profile_order_for = _winner_picker.profile_order_for
_flatten_profile_items = _winner_picker.flatten_profile_items
_redact = _error_redactor.redact
