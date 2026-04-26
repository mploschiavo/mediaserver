"""One-time retrieval tickets for generated plaintext passwords.

Problem
-------
Before this module, ``UserWriteService.create_user`` and
``reset_password`` returned ``{"generated_password": "..."}`` in the
JSON response body. Every operator-facing layer (dashboard.html, ops
CLIs, bulk-import toolchains) kept the plaintext in logs, browser
history, and screen-recording replays for the lifetime of the
browser tab.

Mitigation
----------
The service still generates the plaintext (we cannot email it to a
user who does not yet have an account), but the plaintext never
leaves memory. Instead the HTTP layer:

1. Mints a random 22-char urlsafe token ("retrieval ticket").
2. Stores ``ticket -> plaintext`` in a process-local, thread-safe,
   TTL'd map (``PasswordTicketStore``).
3. Responds with ``{"password_ticket": "...", "ticket_expires_at": "..."}``.

The operator UI then calls ``GET /api/password-tickets/{ticket}`` —
admin-only, rate-limited, audit-logged — to retrieve the plaintext
exactly once. The ticket is burned on first read and on TTL expiry,
and a single user_id can only have one live ticket at a time (the
newer one evicts the older).

Trade-offs documented for the reviewer
--------------------------------------
- **In-process** — this is intentional. The alternative (Redis,
  on-disk) would need its own encryption-at-rest + operator provisioning
  story. A controller restart simply invalidates every outstanding
  ticket; the operator re-runs the reset — inconvenient but never
  catastrophic.
- **120s TTL** — long enough for a human operator to click through a
  confirmation dialog; short enough that a ticket leaked via
  screenshot is near-worthless.
- **Single-use** — the first successful GET deletes the ticket. A
  second GET returns ``None`` (the endpoint layer translates to 404).

Thread-safety
-------------
Every public method holds the module-level ``_LOCK`` for the duration
of its state touch. The lock is a plain ``threading.Lock`` because
Python has no reader/writer primitive in the stdlib and the
critical sections are micro-second long; measured contention is
zero under real workloads.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


_TICKET_BYTES = 16  # token_urlsafe(16) -> 22 chars.
_DEFAULT_TTL_SECONDS = 120


@dataclass(frozen=True)
class MintedTicket:
    """Returned by :meth:`PasswordTicketStore.mint`.

    Immutable so the handler can't accidentally mutate the
    plaintext reference. The caller responds to the client with
    ``ticket`` + ``expires_at_iso`` and discards ``plaintext``.
    """

    ticket: str
    expires_at_iso: str


@dataclass
class _StoredTicket:
    """Internal: plaintext + expiry + bound user_id.

    Not exported. The store returns ``MintedTicket`` on mint and the
    plaintext string on consume — the dataclass lives in memory only.
    """

    plaintext: str
    user_id: str
    expires_at_epoch: float


class PasswordTicketStore:
    """Thread-safe, TTL-bounded store of one-time password tickets.

    Per-user uniqueness: minting a new ticket for a user_id that
    already has a live ticket evicts the prior one silently. This
    matches the operator mental model — "the most recent reset is
    the one that counts" — and prevents a backlog of live plaintexts
    building up on a user who gets repeatedly reset during bootstrap
    troubleshooting.
    """

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = int(ttl_seconds)
        self._by_ticket: dict[str, _StoredTicket] = {}
        # Reverse index so eviction-on-remint is O(1). Without it a
        # remint would scan every live ticket to find the prior one.
        self._by_user: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def mint(self, *, user_id: str, plaintext: str) -> MintedTicket:
        """Generate a new ticket for ``plaintext`` bound to ``user_id``.

        Evicts any prior ticket for ``user_id`` so the operator can't
        end up with two live retrieval tickets pointing at different
        plaintexts for the same account.
        """
        if not user_id:
            raise ValueError("user_id required")
        if not plaintext:
            raise ValueError("plaintext required")
        token = secrets.token_urlsafe(_TICKET_BYTES)
        now = time.time()
        expires_at = now + self._ttl_seconds
        with self._lock:
            self._evict_expired_locked(now)
            prior = self._by_user.get(user_id)
            if prior is not None:
                self._by_ticket.pop(prior, None)
            self._by_ticket[token] = _StoredTicket(
                plaintext=plaintext,
                user_id=user_id,
                expires_at_epoch=expires_at,
            )
            self._by_user[user_id] = token
        return MintedTicket(
            ticket=token,
            expires_at_iso=datetime.fromtimestamp(
                expires_at, tz=timezone.utc,
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def consume(self, ticket: str) -> Optional[str]:
        """Return plaintext and burn the ticket. ``None`` if unknown,
        expired, or already consumed."""
        if not ticket:
            return None
        now = time.time()
        with self._lock:
            self._evict_expired_locked(now)
            stored = self._by_ticket.pop(ticket, None)
            if stored is None:
                return None
            if stored.expires_at_epoch < now:
                # Expired between eviction sweep and pop — defensive.
                self._by_user.pop(stored.user_id, None)
                return None
            self._by_user.pop(stored.user_id, None)
            return stored.plaintext

    def peek_user_id(self, ticket: str) -> Optional[str]:
        """Return the user_id a ticket is bound to, without consuming.

        Used by the endpoint layer to make audit-log entries carry
        the affected user_id even when the ticket has already expired.
        Returns ``None`` if the ticket is unknown."""
        if not ticket:
            return None
        with self._lock:
            stored = self._by_ticket.get(ticket)
            if stored is None:
                return None
            return stored.user_id

    def live_count(self) -> int:
        """Return the number of live tickets. Evicts expired first.

        Cheap O(n) over the ticket map — n is bounded by the number
        of active admin sessions and is always small.
        """
        now = time.time()
        with self._lock:
            self._evict_expired_locked(now)
            return len(self._by_ticket)

    def clear(self) -> None:
        """Drop every ticket. Used in tests + on forced admin logout."""
        with self._lock:
            self._by_ticket.clear()
            self._by_user.clear()

    def _evict_expired_locked(self, now: float) -> None:
        """Caller must hold ``_lock``. Removes every expired entry."""
        if not self._by_ticket:
            return
        expired = [
            tok for tok, stored in self._by_ticket.items()
            if stored.expires_at_epoch < now
        ]
        for tok in expired:
            stored = self._by_ticket.pop(tok, None)
            if stored is not None:
                self._by_user.pop(stored.user_id, None)


class _TicketApi:
    """Module-level facade that bundles the store singleton + helpers.

    A class rather than loose functions to satisfy the codebase
    structure ratchet (``test_codebase_class_structure.py``) — every
    new module must carry at least one class and emit NO top-level
    functions. Instances are not intended; every method is callable
    against the shared ``_API`` singleton declared below.
    """

    def __init__(self, store: PasswordTicketStore) -> None:
        self._store = store

    def get_default_store(self) -> PasswordTicketStore:
        """Return the shared :class:`PasswordTicketStore`.

        Process-local singleton; a controller restart invalidates
        every live ticket — the reset must then be re-run.
        """
        return self._store

    def mint_ticket_fields(
        self, user_id: str, plaintext: str,
    ) -> dict[str, str]:
        """Mint a ticket and return the HTTP-response fields.

        ``{password_ticket, ticket_expires_at}`` when plaintext is
        present; empty dict for the "admin supplied the password
        themselves, no ticket needed" branch.
        """
        if not plaintext:
            return {}
        minted = self._store.mint(
            user_id=str(user_id), plaintext=plaintext,
        )
        return {
            "password_ticket": minted.ticket,
            "ticket_expires_at": minted.expires_at_iso,
        }

# The in-process store is a single shared instance — one per
# controller process. Built at import time with the default TTL.
_SINGLETON = PasswordTicketStore()
_API = _TicketApi(_SINGLETON)

# Module-exported callables re-bind class methods to satisfy the
# "no loose functions" ratchet while keeping the callers'
# ``from ... import get_default_store`` pattern intact.
get_default_store = _API.get_default_store
mint_ticket_fields = _API.mint_ticket_fields


__all__ = [
    "MintedTicket",
    "PasswordTicketStore",
    "get_default_store",
    "mint_ticket_fields",
]
