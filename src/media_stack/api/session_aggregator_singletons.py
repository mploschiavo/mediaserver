"""Lazy singletons for the session aggregator + security reports.

The HTTP layer needs a stable, shared reference to the aggregator
and the security-report service so every handler / every request
sees the same live view (and the same admin counter attribution).
Instantiating eagerly at import time would pull the
``AuditLog``, provider registry, and login-history index into any
process that imports ``media_stack.api`` — including test runs and
CLI tools that never touch sessions.

We follow the same pattern as ``session_singletons``: construct on
first use, expose accessors that tests can override with
``set_*`` helpers.

Wiring sources
--------------
* ``session_store`` is the already-shared singleton from
  ``session_singletons``.
* ``user_service`` supplies the audit log, user providers, and the
  ``LoginHistoryIndex``. The same factory is used by every other
  API handler, so re-using it here keeps the state surface small.

This module is deliberately thin — the heavy lifting lives in the
service classes. If the HTTP layer decides to construct the
services directly for some route, it can; this is the shared
convenience path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from media_stack.api.session_singletons import session_store
from media_stack.services.security.security_report_service import (
    SecurityReportService,
)
from media_stack.services.security.session_aggregator import SessionAggregator

_log = logging.getLogger("media_stack.api.session_aggregator_singletons")

_session_aggregator: SessionAggregator | None = None
_security_report_service: SecurityReportService | None = None


def get_session_aggregator() -> SessionAggregator:
    """Return the shared ``SessionAggregator``, building it on first call.

    Built lazily because construction pulls the provider registry
    and the login-history index into the process — modules that
    only need the session store directly shouldn't pay that cost.
    """
    global _session_aggregator
    if _session_aggregator is None:
        _session_aggregator = _build_session_aggregator()
    return _session_aggregator


def set_session_aggregator(agg: SessionAggregator | None) -> None:
    """Override (or clear) the shared aggregator. For tests only."""
    global _session_aggregator
    _session_aggregator = agg


def get_security_report_service() -> SecurityReportService:
    """Return the shared ``SecurityReportService``, lazy-built."""
    global _security_report_service
    if _security_report_service is None:
        _security_report_service = _build_security_report_service()
    return _security_report_service


def set_security_report_service(svc: SecurityReportService | None) -> None:
    """Override (or clear) the shared reports service. For tests only."""
    global _security_report_service
    _security_report_service = svc


def reset() -> None:
    """Clear every cached singleton — used by tests between cases."""
    set_session_aggregator(None)
    set_security_report_service(None)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_session_aggregator() -> SessionAggregator:
    base_providers, history = _optional_user_service_parts()
    # Augment with the dedicated SessionAdminProvider impls living
    # under services.security.providers. These query Authelia /
    # Jellyfin / Jellyseerr by USERNAME (not external_id), which is
    # the right key under SSO where the controller has no per-user
    # external_id mapping for federated accounts.
    extras = _build_security_session_providers()
    # Dedupe by .name — ``base_providers`` already includes the
    # legacy UserProvider impls that satisfy ``SessionAdminProvider``;
    # ``extras`` shadows them with the username-keyed implementation.
    by_name: dict[str, Any] = {p.name: p for p in base_providers}
    for p in extras:
        by_name[p.name] = p
    return SessionAggregator(
        session_store=session_store,
        providers=list(by_name.values()),
        login_history=history,
        known_usernames=_known_usernames_from_user_store,
    )


def _build_security_session_providers() -> list:
    """Construct the three security/providers session providers.

    Each ``from_env`` factory returns ``None`` when the required
    config (URL + API key for the HTTP-backed providers) is missing.
    Connection failures during the construction-time probe are
    swallowed inside the provider — it just registers as
    ``available=False`` and serves [].
    """
    out: list = []
    try:
        from media_stack.services.security.providers import (
            authelia_session_provider,
            jellyfin_session_provider,
            jellyseerr_session_provider,
        )
    except Exception as exc:  # noqa: BLE001
        _log.info(
            "security.providers package unavailable, skipping: %s", exc,
        )
        return out
    for module in (
        authelia_session_provider,
        jellyfin_session_provider,
        jellyseerr_session_provider,
    ):
        try:
            p = module.from_env()
        except Exception as exc:  # noqa: BLE001
            _log.info(
                "session provider %s construction failed: %s",
                getattr(module, "__name__", "?"), exc,
            )
            continue
        if p is None:
            continue
        out.append(p)
    return out


def _known_usernames_from_user_store() -> list[str]:
    """Return every controller-known username for provider fan-out.

    The aggregator calls this when its controller-session set is
    empty (the SSO case). ``UserStore.list_all`` is the closest
    thing to a "directory" the controller has — every user we've
    ever seen has a row there. Returns ``[]`` and logs at debug if
    the store can't be reached so the aggregator stays best-effort.
    """
    try:
        from media_stack.core.auth.users.user_store import UserStore
        config_root = Path(
            os.environ.get("CONFIG_ROOT", "/srv-config"),
        )
        path = config_root / ".controller" / "users.json"
        if not path.is_file():
            # Fallback path used by some older deployments.
            alt = config_root / "controller" / "users.json"
            if alt.is_file():
                path = alt
        store = UserStore(path)
        return [
            u.username for u in store.list_all() if getattr(u, "username", "")
        ]
    except Exception as exc:  # noqa: BLE001
        _log.debug("known_usernames_from_user_store failed: %s", exc)
        return []


def _build_security_report_service() -> SecurityReportService:
    audit, _providers, history = _user_service_parts()
    return SecurityReportService(
        audit_log=audit,
        session_aggregator=get_session_aggregator(),
        login_history=history,
    )


def _optional_user_service_parts() -> tuple[list, Any | None]:
    """Return (providers, login_history), or ``([], None)`` when the
    user-service factory isn't available (reduced-footprint deploys
    and most unit tests).
    """
    try:
        _audit, providers, history = _user_service_parts()
    except Exception as exc:  # noqa: BLE001
        _log.debug(
            "user-service wiring unavailable, aggregator runs "
            "controller-only: %s", exc,
        )
        return [], None
    return providers, history


def _user_service_parts() -> tuple[Any, list, Any]:
    """Return ``(audit_log, session_providers, login_history)`` pulled
    from the user-service factory.

    Factored out so both builders share one source. Raises if the
    factory can't stand up — the caller decides whether to tolerate
    that failure.
    """
    # Local import avoids pulling the factory (and everything it
    # imports) into the module-load path of callers that only need
    # the session store.
    from media_stack.core.auth.users.audit_log import AuditLog
    from media_stack.core.auth.users.login_history_index import (
        LoginHistoryIndex,
    )
    from media_stack.core.auth.users.user_service_factory import (
        UserServiceFactory,
    )
    from media_stack.core.auth.users.visibility_protocols import (
        SessionAdminProvider,
    )

    factory = UserServiceFactory()
    service = factory.build()
    audit: AuditLog = getattr(service, "_audit", None)
    if audit is None:
        raise RuntimeError("user_service did not expose an audit log")
    raw_providers = list(getattr(service, "_providers", []) or [])
    session_providers = [
        p for p in raw_providers if isinstance(p, SessionAdminProvider)
    ]
    history = LoginHistoryIndex(audit_log=audit, session_store=session_store)
    try:
        history.rebuild()
    except Exception as exc:  # noqa: BLE001
        _log.debug("login_history initial rebuild failed: %s", exc)
    return audit, session_providers, history


__all__ = [
    "get_security_report_service",
    "get_session_aggregator",
    "reset",
    "set_security_report_service",
    "set_session_aggregator",
]
