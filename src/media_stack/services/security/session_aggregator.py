"""Cross-provider aggregator for live-session visibility.

Fans out across the controller's ``SessionStore`` and every
``SessionAdminProvider`` in the stack, producing one deduplicated
``SessionDTO`` list. The HTTP layer (``/api/sessions/active``) then
sees a single list + one authz check instead of 5 provider-specific
probes, and a broken provider can't take down the whole view —
per-call exceptions are swallowed and logged at ``debug``.

Invariants
----------
* ``SessionDTO`` is frozen + JSON-safe; never holds a raw token.
* ``list_all`` is admin-only; ``list_for_user`` is self-or-admin.
* Dedup key ``(provider, session_id)``; providers are disjoint so
  the pair is globally unique.
* Ordering: ``last_activity`` desc, ``connected_since`` desc,
  ``provider`` asc. ISO-Z strings sort lexically in chronological
  order so strings sort directly.
* Device class comes from ``classify_class``; controller sessions
  persist it at mint, provider rows derive it at aggregate time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from media_stack.core.auth.authz import (
    Actor,
    requires_admin,
    requires_self_or_admin,
)
from media_stack.core.auth.users.device_classifier import classify_class
from media_stack.core.auth.users.login_history_index import LoginHistoryProtocol
from media_stack.core.auth.users.visibility_protocols import SessionAdminProvider

_log = logging.getLogger("media_stack.security.session_aggregator")

CONTROLLER_PROVIDER = "controller"


@runtime_checkable
class SessionStoreProtocol(Protocol):
    """Minimal surface needed from the controller's ``SessionStore``.

    Declared locally so the aggregator doesn't hard-couple to the
    concrete class; tests pass a recording fake.
    """

    def list_all_active(self) -> list[Any]: ...

    def list_for(self, username: str) -> list[Any]: ...


@dataclass(frozen=True)
class SessionDTO:
    """Aggregated view of a single session across every provider.

    Attributes
    ----------
    provider:
        Provider discriminant — ``"controller"``, ``"jellyfin"``,
        ``"authelia"``, etc.
    session_id:
        Opaque id scoped within ``provider`` (public id, not hash).
    username:
        Account name. Empty when the provider → controller mapping
        is unknown for this row.
    device_class:
        One of ``TV`` / ``PHONE`` / ``TABLET`` / ``DESKTOP`` / ``CLI``
        / ``UNKNOWN`` so the UI picks a stable icon.
    client_ip:
        Observed client IP at session mint. May be empty for legacy
        sessions that pre-date binding.
    first_seen_ip:
        True iff the ``(user, /24)`` pair was not seen in the
        configured ``LoginHistoryIndex`` lookback. False when no
        index is wired.
    connected_since / last_activity:
        ISO-8601 Z strings. Empty when the provider didn't report.
    revokable:
        False for provider sessions the controller can only read.
    """

    provider: str
    session_id: str
    username: str
    device: str = ""
    device_class: str = ""
    client: str = ""
    client_ip: str = ""
    first_seen_ip: bool = False
    connected_since: str = ""
    last_activity: str = ""
    revokable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "session_id": self.session_id,
            "username": self.username,
            "device": self.device,
            "device_class": self.device_class,
            "client": self.client,
            "client_ip": self.client_ip,
            "first_seen_ip": self.first_seen_ip,
            "connected_since": self.connected_since,
            "last_activity": self.last_activity,
            "revokable": self.revokable,
        }


class _SessionAggregatorHelpers:
    """Module-local helpers grouped as instance methods.

    Per ADR-0012, top-level ``def`` is forbidden in service modules.
    A single ``_INSTANCE`` is constructed at import time and aliased
    to the underscore-prefixed names the rest of the module uses.
    """

    def default_classify(self, source: str) -> str:
        """Default device classifier — ``classify_class`` enum value."""
        if not source:
            return ""
        return classify_class(source).value

    def float_to_iso(self, ts: Any) -> str:
        """Convert a ``Session.last_used_at`` epoch float to ISO-Z.

        Returns "" for zero / missing / non-float inputs so the UI
        treats the session as "never used since mint".
        """
        try:
            f = float(ts)
        except (TypeError, ValueError):
            return ""
        if f <= 0:
            return ""
        return (
            datetime.fromtimestamp(f, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        )

    def dedup_and_sort(
        self, dtos: list[SessionDTO],
    ) -> list[SessionDTO]:
        """Deduplicate by ``(provider, session_id)`` and sort.

        Order: ``last_activity`` desc, ``connected_since`` desc,
        ``provider`` asc. ISO-Z strings sort lexically in chronological
        order so string sort works directly.
        """
        seen: dict[tuple[str, str], SessionDTO] = {}
        for d in dtos:
            # First-wins — controller rows are inserted first and
            # therefore win a tie against a later duplicate provider row.
            seen.setdefault((d.provider, d.session_id), d)
        out = list(seen.values())
        out.sort(
            key=lambda d: (
                self.neg_iso(d.last_activity),
                self.neg_iso(d.connected_since),
                d.provider,
            )
        )
        return out

    def neg_iso(self, s: str) -> tuple:
        """Sort key that orders ``s`` descending under ascending sort.

        ``(-len, inverted)`` puts empty strings last and inverts each
        codepoint so ascending sort = descending chronological order.
        """
        if not s:
            return (1, "")
        inverted = "".join(chr(0x10FFFF - ord(c)) for c in s)
        return (0, inverted)


_INSTANCE = _SessionAggregatorHelpers()
_default_classify = _INSTANCE.default_classify
_float_to_iso = _INSTANCE.float_to_iso
_dedup_and_sort = _INSTANCE.dedup_and_sort
_neg_iso = _INSTANCE.neg_iso


class SessionAggregator:
    """Fan out across every ``SessionAdminProvider`` + the controller's
    own ``SessionStore`` to produce a single deduplicated list.

    Parameters
    ----------
    session_store:
        Controller session store (or a compatible protocol impl).
        Required — controller sessions are always part of the view.
    providers:
        Iterable of ``SessionAdminProvider`` impls. Validated at
        construction against the runtime protocol.
    login_history:
        Optional ``LoginHistoryProtocol`` used to enrich
        ``first_seen_ip``. When absent, that field is always False.
    device_classifier:
        Optional callable mapping a UA / client string to a class
        label. Defaults to ``classify_class`` as a string.
    """

    def __init__(
        self,
        *,
        session_store: SessionStoreProtocol,
        providers: list[SessionAdminProvider] | None = None,
        login_history: LoginHistoryProtocol | None = None,
        device_classifier: Callable[[str], str] | None = None,
        known_usernames: Callable[[], list[str]] | None = None,
    ) -> None:
        if session_store is None:
            raise ValueError("session_store is required")
        providers = list(providers or [])
        for p in providers:
            if not isinstance(p, SessionAdminProvider):
                raise TypeError(
                    f"provider {p!r} does not satisfy SessionAdminProvider "
                    "(needs name, list_sessions, revoke_sessions, "
                    "revoke_session)"
                )
        self._sessions = session_store
        self._providers: dict[str, SessionAdminProvider] = {
            p.name: p for p in providers
        }
        self._login_history = login_history
        self._classify = device_classifier or _default_classify
        # Optional callable that returns the full set of usernames the
        # controller knows about (typically ``UserStore.list_all``
        # mapped to ``.username``). Wired by the singleton layer when
        # SSO is in front of the controller and there are no
        # controller sessions to fan out from. When None, fan-out
        # falls back to "users with a controller session" — the legacy
        # behaviour preserved for unit tests.
        self._known_usernames_source = known_usernames

    # ---- public API ---------------------------------------------------

    @requires_admin
    def list_all(self, *, actor: Actor) -> list[SessionDTO]:
        """Every live session across every provider, newest first.

        Controller sessions come from ``session_store.list_all_active``;
        provider sessions come from each provider's ``list_sessions``
        for every user currently holding a controller session. A
        provider that raises is swallowed; other providers still
        contribute. ``first_seen_ip`` is enriched via the login-
        history index when one is configured.
        """
        dtos = self._collect_controller_sessions()
        dtos.extend(self._collect_provider_sessions_for_all_known_users())
        return _dedup_and_sort(dtos)

    @requires_self_or_admin(param="username")
    def list_for_user(
        self, *, username: str, actor: Actor,
    ) -> list[SessionDTO]:
        """Every live session for ``username``, across providers.

        Same shape + order as ``list_all``, filtered — authz is
        relaxed to self-or-admin so a user can see their own
        devices from the ``/me/sessions`` surface.
        """
        if not username:
            return []
        dtos = self._collect_controller_sessions_for(username)
        dtos.extend(self._collect_provider_sessions_for(username))
        return _dedup_and_sort(dtos)

    # ---- controller-session collection --------------------------------

    def _collect_controller_sessions(self) -> list[SessionDTO]:
        try:
            rows = list(self._sessions.list_all_active())
        except Exception as exc:  # noqa: BLE001 — contract: never raise
            _log.debug("session_store.list_all_active failed: %s", exc)
            return []
        return [self._controller_row_to_dto(r) for r in rows]

    def _collect_controller_sessions_for(
        self, username: str,
    ) -> list[SessionDTO]:
        try:
            rows = list(self._sessions.list_for(username))
        except Exception as exc:  # noqa: BLE001
            _log.debug("session_store.list_for(%r) failed: %s", username, exc)
            return []
        return [self._controller_row_to_dto(r) for r in rows]

    def _controller_row_to_dto(self, sess: Any) -> SessionDTO:
        ua = getattr(sess, "user_agent", "") or ""
        cls = getattr(sess, "device_class", "") or self._classify(ua)
        username = getattr(sess, "owner_username", "") or ""
        client_ip = getattr(sess, "ip_prefix", "") or ""
        last_used = _float_to_iso(getattr(sess, "last_used_at", 0.0))
        return SessionDTO(
            provider=CONTROLLER_PROVIDER,
            session_id=str(getattr(sess, "id", "")),
            username=username,
            device=ua,
            device_class=cls,
            client=ua,
            client_ip=client_ip,
            first_seen_ip=self._is_first_seen(username, client_ip),
            connected_since=str(getattr(sess, "created_at", "")),
            last_activity=last_used,
            revokable=True,
        )

    # ---- provider-session collection ----------------------------------

    def _collect_provider_sessions_for_all_known_users(
        self,
    ) -> list[SessionDTO]:
        """Query each provider for every currently-logged-in user.

        The "currently logged in" set is the union of
        ``owner_username`` across the controller's own sessions —
        a user with no controller session isn't looking at the
        dashboard, so their provider sessions don't surface here.
        Providers that raise are swallowed per-call.
        """
        out: list[SessionDTO] = []
        for username in self._known_usernames():
            out.extend(self._collect_provider_sessions_for(username))
        return out

    def _collect_provider_sessions_for(
        self, username: str,
    ) -> list[SessionDTO]:
        if not username:
            return []
        out: list[SessionDTO] = []
        for name, provider in self._providers.items():
            try:
                rows = list(provider.list_sessions(username))
            except Exception as exc:  # noqa: BLE001
                _log.debug(
                    "provider %s list_sessions(%r) failed: %s",
                    name, username, exc,
                )
                continue
            for ext in rows:
                out.append(self._provider_row_to_dto(name, username, ext))
        return out

    def _provider_row_to_dto(
        self, provider_name: str, username: str, ext: Any,
    ) -> SessionDTO:
        client = str(getattr(ext, "client", "") or "")
        device = str(getattr(ext, "device", "") or "")
        client_ip = str(getattr(ext, "ip", "") or "")
        last_activity = str(getattr(ext, "last_activity", "") or "")
        cls_source = client or device
        return SessionDTO(
            provider=provider_name,
            session_id=str(getattr(ext, "session_id", "") or ""),
            username=username,
            device=device,
            device_class=self._classify(cls_source) if cls_source else "",
            client=client,
            client_ip=client_ip,
            first_seen_ip=self._is_first_seen(username, client_ip),
            connected_since="",
            last_activity=last_activity,
            revokable=True,
        )

    # ---- helpers ------------------------------------------------------

    def _known_usernames(self) -> list[str]:
        seen: dict[str, None] = {}
        # Controller-session-derived names first — these are users
        # the dashboard knows are *currently* on the page, so their
        # provider rows take render priority via dedupe stability.
        try:
            rows = list(self._sessions.list_all_active())
        except Exception as exc:  # noqa: BLE001
            _log.debug("list_all_active (for username set) failed: %s", exc)
            rows = []
        for r in rows:
            u = getattr(r, "owner_username", "") or ""
            if u:
                seen.setdefault(u, None)
        # SSO path: when the controller is fronted by Authelia, the
        # controller has *no* native sessions because every dashboard
        # request rides the trusted-proxy header. Without this
        # fallback the providers above would never get queried.
        if self._known_usernames_source is not None:
            try:
                extra = list(self._known_usernames_source())
            except Exception as exc:  # noqa: BLE001
                _log.debug("known_usernames callable failed: %s", exc)
                extra = []
            for u in extra:
                if u:
                    seen.setdefault(u, None)
        return list(seen.keys())

    def _is_first_seen(self, username: str, client_ip: str) -> bool:
        if not self._login_history or not username or not client_ip:
            return False
        try:
            return bool(
                self._login_history.is_first_seen_ip(username, client_ip)
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "login_history.is_first_seen_ip(%r,%r) failed: %s",
                username, client_ip, exc,
            )
            return False


__all__ = [
    "CONTROLLER_PROVIDER",
    "SessionAggregator",
    "SessionDTO",
    "SessionStoreProtocol",
]
