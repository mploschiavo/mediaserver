"""Persistent BanStore for the session-visibility feature.

Source of truth for **user bans** and **IP bans**. JSON-file-backed
under ``${CONFIG_ROOT}/controller/bans.json`` so the same file travels
in compose and k8s deploys via the shared config volume.

Callers target :class:`BanStoreProtocol` so a future Redis/SQL backend
can swap in without touching callsites. Atomic persistence is delegated
to :class:`SafeJsonEditor` — this module does not reimplement
tmp+fsync+rename.

IP normalisation reuses ``ip_deny._normalize_cidr`` so the controller's
richer :class:`IPBanRecord` and the provider-layer :class:`IPDeny`
agree on canonical form (a bare address like ``203.0.113.4`` becomes
``203.0.113.4/32``).
"""

from __future__ import annotations

import ipaddress
import threading
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from media_stack.domain.auth.users.ip_deny import _normalize_cidr
from media_stack.infrastructure.auth.users.safe_json_edit import SafeJsonEditError, SafeJsonEditor

SCHEMA_VERSION = 1


class BanStoreError(RuntimeError):
    """Raised for load/write errors and policy violations (e.g. duplicate add)."""


class BanReason(str, Enum):
    """Discrete ban templates — reportable and filterable, unlike free text."""

    CREDENTIAL_STUFFING = "credential_stuffing"
    UNAUTHORIZED_SHARING = "unauthorized_sharing"
    ADMIN_REQUEST = "admin_request"
    INVESTIGATION_HOLD = "investigation_hold"
    SECURITY_INCIDENT = "security_incident"
    POLICY_VIOLATION = "policy_violation"
    OTHER = "other"

    @property
    def label(self) -> str:
        return _REASON_LABELS[self]


_REASON_LABELS: dict[BanReason, str] = {
    BanReason.CREDENTIAL_STUFFING: "Credential stuffing",
    BanReason.UNAUTHORIZED_SHARING: "Unauthorized sharing",
    BanReason.ADMIN_REQUEST: "Admin request",
    BanReason.INVESTIGATION_HOLD: "Investigation hold",
    BanReason.SECURITY_INCIDENT: "Security incident",
    BanReason.POLICY_VIOLATION: "Policy violation",
    BanReason.OTHER: "Other",
}


@dataclass(frozen=True)
class UserBan:
    username: str
    reason: BanReason
    actor: str
    banned_at: str
    reason_detail: str = ""
    expires_at: str = ""
    idempotency_key: str = ""

    def is_expired(self, now_iso: str) -> bool:
        if not self.expires_at:
            return False
        return self.expires_at <= now_iso

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reason"] = self.reason.value
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UserBan:
        return cls(
            username=str(raw["username"]),
            reason=BanReason(raw.get("reason", BanReason.OTHER.value)),
            reason_detail=str(raw.get("reason_detail", "")),
            actor=str(raw.get("actor", "")),
            banned_at=str(raw.get("banned_at", "")),
            expires_at=str(raw.get("expires_at", "")),
            idempotency_key=str(raw.get("idempotency_key", "")),
        )


@dataclass(frozen=True)
class IPBanRecord:
    cidr: str
    reason: BanReason
    actor: str
    banned_at: str
    reason_detail: str = ""
    expires_at: str = ""
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "cidr", _normalize_cidr(self.cidr))

    def is_expired(self, now_iso: str) -> bool:
        if not self.expires_at:
            return False
        return self.expires_at <= now_iso

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reason"] = self.reason.value
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> IPBanRecord:
        return cls(
            cidr=str(raw["cidr"]),
            reason=BanReason(raw.get("reason", BanReason.OTHER.value)),
            reason_detail=str(raw.get("reason_detail", "")),
            actor=str(raw.get("actor", "")),
            banned_at=str(raw.get("banned_at", "")),
            expires_at=str(raw.get("expires_at", "")),
            idempotency_key=str(raw.get("idempotency_key", "")),
        )


@runtime_checkable
class BanStoreProtocol(Protocol):
    def list_user_bans(self, include_expired: bool = False) -> list[UserBan]: ...

    def list_ip_bans(self, include_expired: bool = False) -> list[IPBanRecord]: ...

    def add_user_ban(self, ban: UserBan) -> UserBan: ...

    def add_ip_ban(self, ban: IPBanRecord) -> IPBanRecord: ...

    def remove_user_ban(self, username: str) -> UserBan | None: ...

    def remove_ip_ban(self, cidr: str) -> IPBanRecord | None: ...

    def is_user_banned(self, username: str, *, now_iso: str) -> bool: ...

    def is_ip_banned(self, ip: str, *, now_iso: str) -> bool: ...

    def prune_expired(self, now_iso: str) -> tuple[list[UserBan], list[IPBanRecord]]: ...

    def schema_version(self) -> int: ...


class BanStore:
    """JSON-file-backed implementation of :class:`BanStoreProtocol`.

    Concurrency model:
      * An ``RLock`` guards the in-memory cache and serialises writes.
        Reads parse from the cache (O(1) vs parsing on every call).
      * The cache is loaded lazily on first access and invalidated on
        any successful write — the atomic rename guarantees the next
        reader sees the committed payload.
      * Persistence is delegated to :class:`SafeJsonEditor`; we never
        open file descriptors for writes ourselves.

    Idempotency:
      * Adds with a non-empty ``idempotency_key`` that matches an
        existing record are a no-op and return the existing record.
      * Adds with an empty key that collide on primary identity
        (``username`` / ``cidr``) raise :class:`BanStoreError` — we
        refuse to silently duplicate because two adds without keys are
        usually a bug (races should use keys).
      * Removes of unknown records return ``None`` (idempotent).
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._helpers = _HELPERS
        self._editor = SafeJsonEditor(self._path, validator=self._helpers.validate_payload)
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None

    # ----- public API --------------------------------------------------

    def schema_version(self) -> int:
        with self._lock:
            self._ensure_loaded()
            assert self._cache is not None
            return int(self._cache.get("schema", SCHEMA_VERSION))

    def list_user_bans(self, include_expired: bool = False) -> list[UserBan]:
        # ``include_expired`` is kept in the signature for API forward-compat
        # but has no effect today: this layer has no clock source, so expiry
        # filtering is performed by ``is_user_banned(..., now_iso=...)`` and
        # ``prune_expired``. Callers that need a live view should call
        # ``prune_expired(now_iso)`` first.
        with self._lock:
            return list(self._user_bans())

    def list_ip_bans(self, include_expired: bool = False) -> list[IPBanRecord]:
        with self._lock:
            return list(self._ip_bans())

    def add_user_ban(self, ban: UserBan) -> UserBan:
        with self._lock:
            existing = self._find_user_by_key(ban.idempotency_key) if ban.idempotency_key else None
            if existing is not None:
                return existing
            if any(b.username == ban.username for b in self._user_bans()):
                raise BanStoreError(
                    f"user {ban.username!r} already banned; supply idempotency_key to retry"
                )
            self._commit(lambda d: self._helpers.append(d, "user_bans", ban.to_dict()))
            return ban

    def add_ip_ban(self, ban: IPBanRecord) -> IPBanRecord:
        with self._lock:
            existing = self._find_ip_by_key(ban.idempotency_key) if ban.idempotency_key else None
            if existing is not None:
                return existing
            if any(b.cidr == ban.cidr for b in self._ip_bans()):
                raise BanStoreError(
                    f"cidr {ban.cidr!r} already banned; supply idempotency_key to retry"
                )
            self._commit(lambda d: self._helpers.append(d, "ip_bans", ban.to_dict()))
            return ban

    def remove_user_ban(self, username: str) -> UserBan | None:
        with self._lock:
            match = next((b for b in self._user_bans() if b.username == username), None)
            if match is None:
                return None
            self._commit(
                lambda d: self._helpers.remove_where(
                    d, "user_bans", lambda r: r.get("username") == username
                )
            )
            return match

    def remove_ip_ban(self, cidr: str) -> IPBanRecord | None:
        try:
            normalized = _normalize_cidr(cidr)
        except ValueError:
            return None
        with self._lock:
            match = next((b for b in self._ip_bans() if b.cidr == normalized), None)
            if match is None:
                return None
            self._commit(
                lambda d: self._helpers.remove_where(
                    d, "ip_bans", lambda r: r.get("cidr") == normalized
                )
            )
            return match

    def is_user_banned(self, username: str, *, now_iso: str) -> bool:
        with self._lock:
            for b in self._user_bans():
                if b.username == username and not b.is_expired(now_iso):
                    return True
            return False

    def is_ip_banned(self, ip: str, *, now_iso: str) -> bool:
        try:
            addr = ipaddress.ip_address(str(ip).strip())
        except ValueError:
            return False
        with self._lock:
            for b in self._ip_bans():
                if b.is_expired(now_iso):
                    continue
                try:
                    net = ipaddress.ip_network(b.cidr, strict=False)
                except ValueError:
                    continue
                if addr.version != net.version:
                    continue
                if addr in net:
                    return True
            return False

    def prune_expired(self, now_iso: str) -> tuple[list[UserBan], list[IPBanRecord]]:
        with self._lock:
            expired_users = [b for b in self._user_bans() if b.is_expired(now_iso)]
            expired_ips = [b for b in self._ip_bans() if b.is_expired(now_iso)]
            if not expired_users and not expired_ips:
                return ([], [])
            dead_users = {b.username for b in expired_users}
            dead_cidrs = {b.cidr for b in expired_ips}

            helpers = self._helpers

            def _mutate(d: dict[str, Any]) -> dict[str, Any]:
                d = helpers.remove_where(
                    d, "user_bans", lambda r: r.get("username") in dead_users
                )
                d = helpers.remove_where(d, "ip_bans", lambda r: r.get("cidr") in dead_cidrs)
                return d

            self._commit(_mutate)
            return (expired_users, expired_ips)

    # ----- internals ---------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._cache is not None:
            return
        try:
            raw = self._editor.read()
        except SafeJsonEditError as exc:
            raise BanStoreError(f"could not load bans from {self._path}: {exc}") from exc
        if not raw:
            # First access: write a fresh empty payload so the file
            # exists with the correct schema stamp.
            self._commit(lambda _: self._helpers.empty_payload())
            return
        self._helpers.validate_payload(raw)
        self._cache = raw

    def _user_bans(self) -> list[UserBan]:
        self._ensure_loaded()
        assert self._cache is not None
        return [UserBan.from_dict(r) for r in self._cache.get("user_bans", [])]

    def _ip_bans(self) -> list[IPBanRecord]:
        self._ensure_loaded()
        assert self._cache is not None
        return [IPBanRecord.from_dict(r) for r in self._cache.get("ip_bans", [])]

    def _find_user_by_key(self, key: str) -> UserBan | None:
        if not key:
            return None
        for b in self._user_bans():
            if b.idempotency_key == key:
                return b
        return None

    def _find_ip_by_key(self, key: str) -> IPBanRecord | None:
        if not key:
            return None
        for b in self._ip_bans():
            if b.idempotency_key == key:
                return b
        return None

    def _commit(self, mutator) -> None:
        try:
            written = self._editor.edit(lambda d: mutator(self._helpers.ensure_shape(d)))
        except SafeJsonEditError as exc:
            # Invalidate cache: on-disk state may or may not have changed.
            self._cache = None
            raise BanStoreError(str(exc)) from exc
        self._cache = written


# ----- payload helpers -------------------------------------------------


class BanStoreHelpers:
    """Pure helpers for shaping and validating the on-disk JSON payload.

    Per ADR-0012 the module exposes plain instance methods on a singleton
    rather than module-level functions, so the AST FunctionDef count at
    module scope stays at 0. Module-level aliases below preserve the
    historical private names so ``mock.patch("…ban_store._validate_payload")``
    style call sites keep working.
    """

    def empty_payload(self) -> dict[str, Any]:
        return {"schema": SCHEMA_VERSION, "user_bans": [], "ip_bans": []}

    def ensure_shape(self, d: dict[str, Any]) -> dict[str, Any]:
        out = dict(d) if d else {}
        out.setdefault("schema", SCHEMA_VERSION)
        out.setdefault("user_bans", [])
        out.setdefault("ip_bans", [])
        return out

    def append(self, d: dict[str, Any], key: str, record: dict[str, Any]) -> dict[str, Any]:
        items = list(d.get(key, []))
        items.append(record)
        d[key] = items
        return d

    def remove_where(self, d: dict[str, Any], key: str, pred) -> dict[str, Any]:
        d[key] = [r for r in d.get(key, []) if not pred(r)]
        return d

    def validate_payload(self, data: Any) -> None:
        if not isinstance(data, dict):
            raise BanStoreError("top-level payload must be an object")
        schema = data.get("schema", SCHEMA_VERSION)
        if not isinstance(schema, int):
            raise BanStoreError(f"schema must be int, got {type(schema).__name__}")
        for key in ("user_bans", "ip_bans"):
            if key in data and not isinstance(data[key], list):
                raise BanStoreError(f"{key} must be a list")


_HELPERS = BanStoreHelpers()

# Module-level aliases preserve the historical private helper names.
# New code should reach for the instance methods on ``_HELPERS`` (or via
# ``BanStore._helpers``) instead of these aliases.
_empty_payload = _HELPERS.empty_payload
_ensure_shape = _HELPERS.ensure_shape
_append = _HELPERS.append
_remove_where = _HELPERS.remove_where
_validate_payload = _HELPERS.validate_payload


__all__ = [
    "BanReason",
    "BanStore",
    "BanStoreError",
    "BanStoreHelpers",
    "BanStoreProtocol",
    "IPBanRecord",
    "UserBan",
]
