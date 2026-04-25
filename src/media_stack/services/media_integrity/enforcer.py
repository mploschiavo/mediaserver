"""Apply the canonical policy to every Servarr adapter.

The enforcer is the boot-time and scheduler-triggered guardrail
that keeps every *arr's media-management + naming config in sync
with ``contracts/servarr-policy.yaml``. Drift sources it protects
against:

- An admin tweaks a knob via the *arr's web UI and forgets to
  revert it. Next enforcement pass brings it back.
- An upgrade resets a hidden field to a new default. Enforcer
  catches it within the scheduler's 15-minute tick.
- A fresh *arr instance is added to the stack (new media type,
  second Sonarr). Boot-time enforcement applies the policy before
  users hit a knob.

The enforcer **never deletes files** and **never changes quality
profiles destructively** — those are the reconciler's job. This
module only edits config.

Failure mode (intentional): if any adapter refuses a PUT, the
enforcer continues through the remaining adapters and aggregates
failures into the report. We do NOT abort the pass on first
failure because a transient Radarr outage should not stop Sonarr
from being brought into compliance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from media_stack.core.auth.users.audit_actions import (
    MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED,
    MEDIA_INTEGRITY_CONFIG_ENFORCED,
)
from media_stack.core.events import (
    EventBus,
    MediaIntegrityConfigEnforced,
    MediaIntegrityConfigEnforceFailed,
)
from media_stack.services.media_integrity.arr_protocol import ArrApp
from media_stack.services.media_integrity.policy import ServarrPolicy


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnforceResult:
    """Per-adapter outcome from a single enforcement pass."""

    app: str
    mediamanagement_changed_fields: tuple[str, ...] = ()
    naming_changed_fields: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()  # human-readable; redacted


@dataclass(frozen=True)
class EnforceReport:
    """Aggregate outcome across every adapter in one enforcement pass."""

    results: tuple[EnforceResult, ...] = ()
    total_fields_changed: int = 0
    total_failures: int = 0


class _AuditSink:
    """Protocol-lite for the audit log. Only the three methods we use.

    Kept duck-typed so tests can pass a list-backed fake without
    importing the real AuditLog's on-disk apparatus.
    """

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
        ...  # pragma: no cover — structural typing only


class ServarrConfigEnforcer:
    """Applies ``ServarrPolicy`` to a set of ``ArrApp`` adapters.

    Constructed once at boot with the policy + audit + event bus;
    ``apply()`` is called per scheduler tick or on-demand via the
    ``POST /api/media-integrity/enforce-config`` endpoint.
    """

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

    def apply(self, adapters: list[ArrApp], *, actor: str = "system") -> EnforceReport:
        results: list[EnforceResult] = []
        total_changed = 0
        total_failures = 0
        for adapter in adapters:
            result = self._apply_one(adapter, actor=actor)
            results.append(result)
            total_changed += len(result.mediamanagement_changed_fields) + len(
                result.naming_changed_fields
            )
            total_failures += len(result.failures)
        return EnforceReport(
            results=tuple(results),
            total_fields_changed=total_changed,
            total_failures=total_failures,
        )

    # ------------------------------------------------------------------

    def _apply_one(self, adapter: ArrApp, *, actor: str) -> EnforceResult:
        failures: list[str] = []
        mm_changed = self._apply_section(
            adapter,
            actor=actor,
            section="mediamanagement",
            get_fn=adapter.get_media_management,
            put_fn=adapter.put_media_management,
            build_patch=lambda: self._policy.build_media_management_patch(adapter),
            failures=failures,
        )
        naming_changed = self._apply_section(
            adapter,
            actor=actor,
            section="naming",
            get_fn=adapter.get_naming,
            put_fn=adapter.put_naming,
            build_patch=lambda: self._policy.build_naming_patch(adapter),
            failures=failures,
        )
        all_changed = mm_changed + naming_changed
        sections_applied: list[str] = []
        if not any(f.startswith("mediamanagement:") for f in failures):
            sections_applied.append("mediamanagement")
        if not any(f.startswith("naming:") for f in failures):
            sections_applied.append("naming")
        self._emit_success(
            adapter,
            actor=actor,
            fields_changed=all_changed,
            sections_applied=tuple(sections_applied),
        )
        return EnforceResult(
            app=adapter.name,
            mediamanagement_changed_fields=mm_changed,
            naming_changed_fields=naming_changed,
            failures=tuple(failures),
        )

    def _apply_section(
        self,
        adapter: ArrApp,
        *,
        actor: str,
        section: str,
        get_fn,
        put_fn,
        build_patch,
        failures: list[str],
    ) -> tuple[str, ...]:
        try:
            current = get_fn()
        except Exception as exc:
            self._record_failure(adapter, actor, section, exc, failures)
            return ()
        patch = build_patch()
        if not patch:
            return ()
        changed: list[str] = []
        merged = dict(current)
        for key, value in patch.items():
            if merged.get(key) != value:
                merged[key] = value
                changed.append(key)
        if not changed:
            return ()
        try:
            put_fn(merged)
        except Exception as exc:
            self._record_failure(adapter, actor, section, exc, failures)
            return ()
        return tuple(changed)

    # ------------------------------------------------------------------
    # Event / audit plumbing
    # ------------------------------------------------------------------

    def _emit_success(
        self,
        adapter: ArrApp,
        *,
        actor: str,
        fields_changed: tuple[str, ...],
        sections_applied: tuple[str, ...],
    ) -> None:
        if self._bus is not None:
            try:
                self._bus.publish(
                    MediaIntegrityConfigEnforced(
                        app=adapter.name,
                        fields_changed=fields_changed,
                        sections_applied=sections_applied,
                    )
                )
            except Exception:  # pragma: no cover — bus must not break enforcement
                logger.debug("event bus refused config_enforced", exc_info=True)
        if self._audit is not None:
            try:
                self._audit.append(
                    actor=actor,
                    action=MEDIA_INTEGRITY_CONFIG_ENFORCED,
                    target=adapter.name,
                    detail={
                        "fields_changed": list(fields_changed),
                        "sections_applied": list(sections_applied),
                    },
                )
            except Exception:  # pragma: no cover
                logger.debug("audit refused config_enforced", exc_info=True)

    def _record_failure(
        self,
        adapter: ArrApp,
        actor: str,
        section: str,
        exc: Exception,
        failures: list[str],
    ) -> None:
        error = _redact(str(exc))
        failures.append(f"{section}: {error}")
        if self._bus is not None:
            try:
                self._bus.publish(
                    MediaIntegrityConfigEnforceFailed(
                        app=adapter.name, section=section, error=error
                    )
                )
            except Exception:  # pragma: no cover
                logger.debug("event bus refused enforce_failed", exc_info=True)
        if self._audit is not None:
            try:
                self._audit.append(
                    actor=actor,
                    action=MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED,
                    target=adapter.name,
                    result="failure",
                    detail={"section": section, "error": error},
                )
            except Exception:  # pragma: no cover
                logger.debug("audit refused enforce_failed", exc_info=True)


def _redact(text: str) -> str:
    """Drop ``X-Api-Key`` / ``apikey`` / url-embedded secrets from
    error strings before they reach the audit log. Defensive — the
    Servarr error body echoes the request context in some versions."""
    if not text:
        return ""
    # A conservative scrub: if a string looks like ``apikey=...`` or
    # has a 32+ hex run (typical *arr key shape), strip it.
    import re

    redacted = re.sub(r"(?i)(apikey|api_key|x-api-key)\s*[=:]\s*\S+", r"\1=REDACTED", text)
    redacted = re.sub(r"[a-f0-9]{32,}", "REDACTED", redacted)
    return redacted[:500]  # cap runaway errors
