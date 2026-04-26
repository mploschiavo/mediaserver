"""Reconcile duplicate subtitles across Bazarr.

Sibling to ``reconciler.py``. Different grouping, same spirit:
heal silently, surface only the genuinely ambiguous cases to the
UI as a calm "needs review" chip.

Grouping
--------
Subtitles are duplicates when they share the 5-tuple
``(release_id, release_kind, language, forced, hi)``. The 5-tuple
is chosen because:

- Same language is an obvious dupe axis.
- Forced + HI flags represent *legitimately distinct* subtitles
  (``.en.srt`` vs. ``.en.forced.srt`` are NOT dupes — the forced
  track contains only subs for foreign dialogue, not a full
  transcript). We group by all 3 flags so we never delete a
  legitimate variant.
- (release_id, release_kind) disambiguate movie subs from episode
  subs; the same language+flags across two different movies aren't
  duplicates.

Winner-picking
--------------
1. Highest ``subtitle_score`` first. (Bazarr's own 0-100 rating.)
2. Earliest ``added_at`` wins ties (stability — later arrival was
   the surprise).
3. Smallest ``size`` wins remaining ties (smaller sub = cleaner
   source usually).

If all three tie → emit ``MediaIntegrityDuplicateReviewNeeded``.

Enforcer hand-off
-----------------
The Bazarr settings enforcer lives in this same module (as
``enforce_bazarr_settings``) because it shares the dotted-path
merge logic with the reconciler. The Servarr enforcer is in
``enforcer.py``; keeping them separate avoids an ``if isinstance``
ladder in one monolithic enforcer.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from media_stack.core.auth.users.audit_actions import (
    MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED,
    MEDIA_INTEGRITY_CONFIG_ENFORCED,
    MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
    MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED,
    MEDIA_INTEGRITY_RECONCILE_FAILED,
)
from media_stack.core.events import (
    EventBus,
    MediaIntegrityConfigEnforced,
    MediaIntegrityConfigEnforceFailed,
    MediaIntegrityDuplicateResolved,
    MediaIntegrityDuplicateReviewNeeded,
    MediaIntegrityReconcileFailed,
)
from media_stack.domain.media_integrity.bazarr_protocol import (
    BazarrApp,
    SubtitleFile,
    SubtitleRelease,
)
from media_stack.domain.media_integrity.policy import ServarrPolicy


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubtitleResolution:
    release_id: str
    release_kind: str
    release_title: str
    language: str
    forced: bool
    hi: bool
    winner_path: str
    loser_paths: tuple[str, ...]
    bytes_freed: int


@dataclass(frozen=True)
class SubtitleReview:
    release_id: str
    release_kind: str
    release_title: str
    language: str
    forced: bool
    hi: bool
    candidate_paths: tuple[str, ...]


@dataclass(frozen=True)
class BazarrReconcileReport:
    resolved: tuple[SubtitleResolution, ...] = ()
    needs_review: tuple[SubtitleReview, ...] = ()
    failures: tuple[str, ...] = ()
    total_bytes_freed: int = 0
    dry_run: bool = False


@dataclass(frozen=True)
class BazarrEnforceReport:
    changed_paths: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------


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
        ...  # pragma: no cover


class BazarrSubtitleReconciler:

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
        adapter: BazarrApp,
        *,
        actor: str = "system",
        dry_run: bool = False,
    ) -> BazarrReconcileReport:
        resolved: list[SubtitleResolution] = []
        needs_review: list[SubtitleReview] = []
        failures: list[str] = []
        try:
            releases = adapter.list_subtitle_releases()
        except Exception as exc:
            self._record_failure(adapter, "", exc, failures, actor=actor)
            return BazarrReconcileReport(failures=tuple(failures), dry_run=dry_run)
        for release in releases:
            try:
                outcomes = self._reconcile_release(
                    adapter, release, actor=actor, dry_run=dry_run
                )
            except Exception as exc:
                self._record_failure(adapter, release.id, exc, failures, actor=actor)
                continue
            for outcome in outcomes:
                if isinstance(outcome, SubtitleResolution):
                    resolved.append(outcome)
                else:
                    needs_review.append(outcome)
        return BazarrReconcileReport(
            resolved=tuple(resolved),
            needs_review=tuple(needs_review),
            failures=tuple(failures),
            total_bytes_freed=sum(r.bytes_freed for r in resolved),
            dry_run=dry_run,
        )

    def _reconcile_release(
        self,
        adapter: BazarrApp,
        release: SubtitleRelease,
        *,
        actor: str,
        dry_run: bool,
    ) -> list[SubtitleResolution | SubtitleReview]:
        subs = adapter.list_subtitles_for(release.id, release.kind)
        groups = _group_subtitles(subs)
        out: list[SubtitleResolution | SubtitleReview] = []
        for (lang, forced, hi), members in groups.items():
            if len(members) < 2:
                continue
            winner, losers = _pick_subtitle_winner(members, adapter)
            if winner is None:
                review = self._emit_review(
                    adapter,
                    release,
                    language=lang,
                    forced=forced,
                    hi=hi,
                    candidates=members,
                    actor=actor,
                )
                out.append(review)
                continue
            bytes_freed = 0
            deleted_paths: list[str] = []
            for loser in losers:
                if dry_run:
                    bytes_freed += loser.size
                    deleted_paths.append(loser.path)
                    continue
                try:
                    adapter.delete_subtitle(loser)
                except Exception as exc:
                    logger.debug(
                        "bazarr.delete_subtitle failed on %s: %s",
                        loser.path,
                        exc,
                    )
                    continue
                bytes_freed += loser.size
                deleted_paths.append(loser.path)
            if not deleted_paths:
                review = self._emit_review(
                    adapter,
                    release,
                    language=lang,
                    forced=forced,
                    hi=hi,
                    candidates=members,
                    actor=actor,
                )
                out.append(review)
                continue
            resolution = SubtitleResolution(
                release_id=release.id,
                release_kind=release.kind,
                release_title=release.title,
                language=lang,
                forced=forced,
                hi=hi,
                winner_path=winner.path,
                loser_paths=tuple(deleted_paths),
                bytes_freed=bytes_freed,
            )
            if not dry_run:
                self._emit_resolved(adapter, resolution, actor=actor)
            out.append(resolution)
        return out

    # -- events / audit -------------------------------------------------

    def _emit_resolved(
        self,
        adapter: BazarrApp,
        resolution: SubtitleResolution,
        *,
        actor: str,
    ) -> None:
        if self._bus is not None:
            try:
                self._bus.publish(
                    MediaIntegrityDuplicateResolved(
                        app=adapter.name,
                        release_id=f"{resolution.release_kind}:{resolution.release_id}",
                        release_title=resolution.release_title,
                        winner_file_id=resolution.winner_path,
                        loser_file_ids=resolution.loser_paths,
                        total_bytes_freed=resolution.bytes_freed,
                    )
                )
            except Exception:  # pragma: no cover
                logger.debug("bus refused subtitle_resolved", exc_info=True)
        if self._audit is not None:
            try:
                self._audit.append(
                    actor=actor,
                    action=MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
                    target=f"{adapter.name}:{resolution.release_kind}:{resolution.release_id}:{resolution.language}",
                    detail={
                        "release_title": resolution.release_title,
                        "language": resolution.language,
                        "forced": resolution.forced,
                        "hi": resolution.hi,
                        "winner_path": resolution.winner_path,
                        "loser_paths": list(resolution.loser_paths),
                        "bytes_freed": resolution.bytes_freed,
                    },
                )
            except Exception:  # pragma: no cover
                logger.debug("audit refused subtitle_resolved", exc_info=True)

    def _emit_review(
        self,
        adapter: BazarrApp,
        release: SubtitleRelease,
        *,
        language: str,
        forced: bool,
        hi: bool,
        candidates: list[SubtitleFile],
        actor: str,
    ) -> SubtitleReview:
        candidate_paths = tuple(c.path for c in candidates)
        review = SubtitleReview(
            release_id=release.id,
            release_kind=release.kind,
            release_title=release.title,
            language=language,
            forced=forced,
            hi=hi,
            candidate_paths=candidate_paths,
        )
        if self._bus is not None:
            try:
                self._bus.publish(
                    MediaIntegrityDuplicateReviewNeeded(
                        app=adapter.name,
                        release_id=f"{release.kind}:{release.id}",
                        release_title=release.title,
                        candidate_file_ids=candidate_paths,
                    )
                )
            except Exception:  # pragma: no cover
                logger.debug("bus refused subtitle_review", exc_info=True)
        if self._audit is not None:
            try:
                self._audit.append(
                    actor=actor,
                    action=MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED,
                    target=f"{adapter.name}:{release.kind}:{release.id}:{language}",
                    detail={
                        "release_title": release.title,
                        "language": language,
                        "forced": forced,
                        "hi": hi,
                        "candidate_paths": list(candidate_paths),
                    },
                )
            except Exception:  # pragma: no cover
                logger.debug("audit refused subtitle_review", exc_info=True)
        return review

    def _record_failure(
        self,
        adapter: BazarrApp,
        release_id: str,
        exc: Exception,
        failures: list[str],
        *,
        actor: str,
    ) -> None:
        error = _redact(str(exc))
        failures.append(f"{release_id or '*'}: {error}")
        if self._bus is not None:
            try:
                self._bus.publish(
                    MediaIntegrityReconcileFailed(
                        app=adapter.name, release_id=release_id, error=error
                    )
                )
            except Exception:  # pragma: no cover
                logger.debug("bus refused subtitle_reconcile_failed", exc_info=True)
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
                logger.debug("audit refused subtitle_reconcile_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Bazarr settings enforcer
# ---------------------------------------------------------------------------


class BazarrSettingsEnforcer:

    def __init__(
        self,
        *,
        policy: ServarrPolicy,
        audit: _AuditSink | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._policy = policy
        self._audit = audit
        self._bus = event_bus

    def apply(self, adapter: BazarrApp, *, actor: str = "system") -> BazarrEnforceReport:
        failures: list[str] = []
        try:
            current = adapter.get_settings()
        except Exception as exc:
            error = _redact(str(exc))
            failures.append(f"settings: {error}")
            self._emit_failed(adapter, error=error, actor=actor)
            return BazarrEnforceReport(failures=tuple(failures))
        patch = self._policy.build_bazarr_settings_patch(adapter)
        if not patch:
            return BazarrEnforceReport()
        changed: list[str] = []
        merged = _deep_copy_dict(current)
        for dotted_path, value in patch.items():
            before = _get_dotted(merged, dotted_path)
            if before != value:
                _set_dotted(merged, dotted_path, value)
                changed.append(dotted_path)
        if not changed:
            return BazarrEnforceReport()
        try:
            adapter.put_settings(merged)
        except Exception as exc:
            error = _redact(str(exc))
            failures.append(f"settings: {error}")
            self._emit_failed(adapter, error=error, actor=actor)
            return BazarrEnforceReport(failures=tuple(failures))
        self._emit_enforced(adapter, fields_changed=tuple(changed), actor=actor)
        return BazarrEnforceReport(changed_paths=tuple(changed))

    def _emit_enforced(
        self,
        adapter: BazarrApp,
        *,
        fields_changed: tuple[str, ...],
        actor: str,
    ) -> None:
        if self._bus is not None:
            try:
                self._bus.publish(
                    MediaIntegrityConfigEnforced(
                        app=adapter.name,
                        fields_changed=fields_changed,
                        sections_applied=("settings",),
                    )
                )
            except Exception:  # pragma: no cover
                logger.debug("bus refused bazarr_enforced", exc_info=True)
        if self._audit is not None:
            try:
                self._audit.append(
                    actor=actor,
                    action=MEDIA_INTEGRITY_CONFIG_ENFORCED,
                    target=adapter.name,
                    detail={
                        "fields_changed": list(fields_changed),
                        "sections_applied": ["settings"],
                    },
                )
            except Exception:  # pragma: no cover
                logger.debug("audit refused bazarr_enforced", exc_info=True)

    def _emit_failed(self, adapter: BazarrApp, *, error: str, actor: str) -> None:
        if self._bus is not None:
            try:
                self._bus.publish(
                    MediaIntegrityConfigEnforceFailed(
                        app=adapter.name, section="settings", error=error
                    )
                )
            except Exception:  # pragma: no cover
                logger.debug("bus refused bazarr_enforce_failed", exc_info=True)
        if self._audit is not None:
            try:
                self._audit.append(
                    actor=actor,
                    action=MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED,
                    target=adapter.name,
                    result="failure",
                    detail={"section": "settings", "error": error},
                )
            except Exception:  # pragma: no cover
                logger.debug("audit refused bazarr_enforce_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable in isolation)
# ---------------------------------------------------------------------------


def _group_subtitles(
    subs: Iterable[SubtitleFile],
) -> dict[tuple[str, bool, bool], list[SubtitleFile]]:
    """Group by ``(language, forced, hi)``. ``release_id`` /
    ``release_kind`` are invariant across all subs in a single
    ``list_subtitles_for`` call so we key only on the 3 axes that
    distinguish legitimate variants."""
    groups: dict[tuple[str, bool, bool], list[SubtitleFile]] = defaultdict(list)
    for sub in subs:
        groups[(sub.language, sub.forced, sub.hi)].append(sub)
    return groups


def _pick_subtitle_winner(
    members: list[SubtitleFile], adapter: BazarrApp
) -> tuple[SubtitleFile | None, list[SubtitleFile]]:
    if not members:
        return None, []
    if len(members) == 1:
        return members[0], []

    def sort_key(s: SubtitleFile) -> tuple[int, str, int]:
        return (-adapter.subtitle_score(s), s.added_at, s.size)

    ordered = sorted(members, key=sort_key)
    top = ordered[0]
    runner = ordered[1]
    if (
        adapter.subtitle_score(top) == adapter.subtitle_score(runner)
        and top.added_at == runner.added_at
        and top.size == runner.size
    ):
        return None, []
    return top, ordered[1:]


def _get_dotted(obj: dict[str, Any], path: str) -> Any:
    parts = path.split(".")
    cur: Any = obj
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _set_dotted(obj: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: dict[str, Any] = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _deep_copy_dict(obj: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if isinstance(v, dict):
            out[k] = _deep_copy_dict(v)
        elif isinstance(v, list):
            out[k] = list(v)
        else:
            out[k] = v
    return out


def _redact(text: str) -> str:
    if not text:
        return ""
    import re
    redacted = re.sub(r"(?i)(apikey|api_key|x-api-key)\s*[=:]\s*\S+", r"\1=REDACTED", text)
    redacted = re.sub(r"[a-f0-9]{32,}", "REDACTED", redacted)
    return redacted[:500]
