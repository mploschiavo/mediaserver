"""IPDenyProvider — stack-level IP-range deny list.

This protocol is deliberately separate from the per-user protocols
in ``visibility_protocols.py`` because IP deny is provider-global,
not per-user. The canonical implementor is the Authelia
file-backed provider, which merges the deny list into
``configuration.yml``'s ``access_control.rules`` so Envoy's
ext_authz hook enforces it at the edge.

Providers without a policy surface (Jellyfin, *arrs, Jellyseerr)
don't implement this protocol. IP-level enforcement for them
happens at the gateway (Envoy + Authelia), not the app.

``IPDeny`` carries an optional ``expires_at``; the controller's
BanStore is responsible for removing expired entries on a timer
and calling ``remove_ip_deny(cidr)`` on each provider — the
providers themselves do not time-expire rules (they'd need a
background clock the file backend doesn't have).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class IPDeny:
    """An IP-or-CIDR deny entry.

    ``cidr`` is normalized at construction: a bare address like
    "203.0.113.45" becomes "203.0.113.45/32" (v4) or the
    canonical "/128" for v6. Validators reject malformed input
    early so the provider never writes garbage into gateway config.

    ``expires_at`` is an ISO-8601 UTC timestamp or the empty string
    (indefinite). Comparisons are string-lexical — callers must
    supply zulu-normalized strings (use
    ``core.auth.time_utils.utcnow_iso``).
    """

    cidr: str
    reason: str = ""
    actor: str = ""
    banned_at: str = ""
    expires_at: str = ""

    def __post_init__(self) -> None:
        normalized = _normalize_cidr(self.cidr)
        # __post_init__ on a frozen dataclass can't assign directly —
        # use object.__setattr__ to replace the raw input with the
        # normalized form.
        object.__setattr__(self, "cidr", normalized)

    def is_expired(self, now_iso: str) -> bool:
        if not self.expires_at:
            return False
        return self.expires_at <= now_iso

    def to_dict(self) -> dict[str, Any]:
        return {
            "cidr": self.cidr,
            "reason": self.reason,
            "actor": self.actor,
            "banned_at": self.banned_at,
            "expires_at": self.expires_at,
        }


def _normalize_cidr(raw: str) -> str:
    """Accept a bare address or CIDR, return canonical form.

    Raises ``ValueError`` on malformed input. The exception is
    explicit rather than e.g. returning ``None`` because an invalid
    ban record is a hard bug — silently dropping it leaves a
    would-be-banned IP unbanned.
    """
    s = str(raw).strip()
    if not s:
        raise ValueError("empty cidr")
    try:
        if "/" in s:
            net = ipaddress.ip_network(s, strict=False)
            return str(net)
        addr = ipaddress.ip_address(s)
        suffix = "/32" if addr.version == 4 else "/128"
        return str(ipaddress.ip_network(f"{addr}{suffix}", strict=False))
    except ValueError as exc:
        raise ValueError(f"invalid cidr {s!r}: {exc}") from exc


@runtime_checkable
class IPDenyProvider(Protocol):
    """Provider-level IP-range deny list.

    Implementors MUST:
      * Persist atomically — partial writes corrupt gateway config.
      * Make ``add_ip_deny`` idempotent: a duplicate cidr replaces
        the existing entry rather than appending.
      * Make ``remove_ip_deny`` tolerate unknown cidr (no-op).
      * Return the *current* persisted list from ``list_ip_denies``
        rather than a cached view.

    IP normalisation is the caller's responsibility via the
    ``IPDeny`` dataclass (which normalises in ``__post_init__``).
    """

    name: str

    def list_ip_denies(self) -> list[IPDeny]: ...

    def add_ip_deny(self, rule: IPDeny) -> None: ...

    def remove_ip_deny(self, cidr: str) -> None: ...


__all__ = ["IPDeny", "IPDenyProvider"]
