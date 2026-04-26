"""Routing config migration v1 → v2.

The v1 shape was a flat dict that grew over many releases. v2 is the
structured shape in ``schema_v2`` (hostnames as a list, explicit
apex/catch-all/aliases, structured cert references). This module
converts v1 inputs into a ``RoutingConfigV2`` so the controller can
read either shape during the rollout window.

Detection:
    * If the dict has ``version`` >= 2 OR contains v2-only keys
      (``hosts``, ``path_aliases``, ``apex``, ``catch_all``, ``certs``,
      ``exposure``, ``defaults``), it's already v2 — pass through.
    * Otherwise migrate.

Mapping (v1 → v2):
    * Top-level fields (base_domain, stack_subdomain, gateway_host,
      gateway_port, app_path_prefix, strategy, scheme) carry across
      verbatim.
    * ``internet_exposed`` (bool) → ``exposure.enabled``. ``binding``
      defaults to ``auto``; the runtime detects K8s/compose at apply.
    * ``direct_hosts`` (``{role: hostname}``) → one ``HostEntry`` per
      role. Role-name → service_id resolution mirrors the existing
      controller logic (``media_server`` → media-server tech;
      everything else is treated as a literal service_id).
    * apex/catch_all/path_aliases/certs/defaults default to empty —
      v1 didn't expose them.

This is a *pure* function (no I/O, no global state). Tested via
``test_routing_migrator.py``.
"""

from __future__ import annotations

from typing import Any

from .schema_v2 import (
    AuthGate,
    Binding,
    ExposureConfig,
    HostAuth,
    HostEntry,
    RoutingConfigV2,
    Strategy,
    _coerce_bool,
    _coerce_enum,
    _coerce_int,
    _coerce_str,
)


# v2-only keys that, if present, mark a dict as already-v2.
_V2_KEYS = frozenset(
    {"hosts", "path_aliases", "apex", "catch_all", "certs", "exposure", "defaults"}
)


def _is_v2(d: dict[str, Any]) -> bool:
    if _coerce_int(d.get("version"), 1) >= 2:
        return True
    return any(k in d for k in _V2_KEYS)


def _resolve_service_id(role: str, media_server_id: str | None) -> str:
    """Map a v1 ``direct_hosts`` role string to a registry service_id.

    The legacy ``role`` is sometimes semantic (``media_server``,
    ``auth``) and sometimes already a service id (``sonarr``,
    ``radarr``). The controller resolved ``media_server`` →
    media-server tech via the profile; we replicate that here when the
    caller provides ``media_server_id``. Roles we don't recognise are
    treated as literal service IDs (the v1 behaviour).
    """
    if role == "media_server":
        return media_server_id or "jellyfin"
    if role == "auth":
        # The auth role can be authelia or other providers; pick the
        # canonical name. Validation will catch a non-existent service.
        return "authelia"
    return role


def _hosts_from_direct_hosts(
    direct_hosts: Any,
    media_server_id: str | None,
    auth_gate_default: AuthGate,
) -> list[HostEntry]:
    """Convert the v1 ``direct_hosts: {role: hostname}`` dict to a list
    of ``HostEntry``. Empty/falsy hostnames are dropped — they were the
    v1 way to "unset" a role without removing the key."""
    out: list[HostEntry] = []
    if not isinstance(direct_hosts, dict):
        return out
    for role, hostname in direct_hosts.items():
        host_str = _coerce_str(hostname).strip()
        if not host_str:
            continue
        service_id = _resolve_service_id(_coerce_str(role), media_server_id)
        # The auth provider must never gate itself with itself, else
        # operators get locked out. v1 implicitly enforced this; v2
        # makes it explicit.
        gate = (
            AuthGate.NONE if service_id == "authelia" else auth_gate_default
        )
        out.append(
            HostEntry(
                role=_coerce_str(role),
                service_id=service_id,
                canonical=host_str,
                aliases=[],
                auth=HostAuth(gate=gate, provider="authelia") if gate != AuthGate.NONE else HostAuth(gate=AuthGate.NONE),
            ),
        )
    # Stable order — sort by role so the migration is deterministic
    # (helps round-trip tests and golden-file diffs).
    out.sort(key=lambda h: h.role)
    return out


def migrate_v1_to_v2(
    raw: dict[str, Any] | None,
    *,
    media_server_id: str | None = None,
    auth_gate_default: AuthGate = AuthGate.REQUIRED,
) -> RoutingConfigV2:
    """Convert any version of the persisted routing dict to a
    ``RoutingConfigV2``. Already-v2 inputs round-trip via
    ``RoutingConfigV2.from_dict``. v1 inputs get a structured shape
    populated from the legacy fields.

    Args:
        raw: the dict as loaded from ``routing.yaml`` (or empty/None).
        media_server_id: profile's media-server selection (jellyfin,
            plex, …) — used when migrating ``direct_hosts.media_server``
            to a concrete service_id. ``None`` falls through to
            ``jellyfin``.
        auth_gate_default: default ``HostEntry.auth.gate`` for
            non-auth services. v1 routed everything through Envoy's
            global ext_authz, so ``REQUIRED`` is faithful; pass
            ``NONE`` to migrate without claiming the auth contract.
    """
    raw_dict: dict[str, Any] = dict(raw or {})

    if _is_v2(raw_dict):
        return RoutingConfigV2.from_dict(raw_dict)

    # -- v1 → v2 migration --
    cfg = RoutingConfigV2(
        version=2,
        base_domain=_coerce_str(raw_dict.get("base_domain"), "local"),
        stack_subdomain=_coerce_str(raw_dict.get("stack_subdomain"), "media-stack"),
        gateway_host=_coerce_str(raw_dict.get("gateway_host"), "apps.media-stack.local"),
        gateway_port=_coerce_int(raw_dict.get("gateway_port"), 80),
        strategy=_coerce_enum(Strategy, raw_dict.get("strategy"), Strategy.HYBRID),  # type: ignore[arg-type]
        scheme=_coerce_str(raw_dict.get("scheme")),
        app_path_prefix=_coerce_str(raw_dict.get("app_path_prefix"), "/app"),
        exposure=ExposureConfig(
            enabled=_coerce_bool(raw_dict.get("internet_exposed"), False),
            binding=Binding.AUTO,
            public_hostnames=[],
            bind_addresses=[],
        ),
        hosts=_hosts_from_direct_hosts(
            raw_dict.get("direct_hosts"),
            media_server_id=media_server_id,
            auth_gate_default=auth_gate_default,
        ),
    )

    # If the gateway_host wasn't already in any host's canonical/aliases,
    # populate exposure.public_hostnames so the v2 view has a starting
    # point. v1 implicitly treated gateway_host as "the public face";
    # losing that on migration would be a regression.
    known_hostnames: set[str] = set()
    for h in cfg.hosts:
        if h.canonical:
            known_hostnames.add(h.canonical)
        known_hostnames.update(h.aliases)
    if cfg.gateway_host and cfg.exposure.enabled:
        cfg.exposure.public_hostnames = sorted(
            known_hostnames | {cfg.gateway_host},
        )
    elif cfg.exposure.enabled:
        cfg.exposure.public_hostnames = sorted(known_hostnames)

    return cfg
