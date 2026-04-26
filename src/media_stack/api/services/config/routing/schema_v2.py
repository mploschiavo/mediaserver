"""Routing v2 — persisted schema.

Pure dataclasses + string enums + defensive ``from_dict``/``to_dict``
helpers. No I/O, no validation. The shape here is the one the
operator edits via the UI and the controller writes to
``routing-overrides.yaml``.

Design notes:

* String enums (subclassing ``str, Enum``) round-trip cleanly to YAML
  without bespoke encoders. ``yaml.safe_load`` returns plain strings;
  we coerce in ``from_dict``.
* Sub-dataclasses are nullable when ``None`` carries semantic meaning
  ("inherit from defaults" vs. "explicit override"). Empty list/dict
  defaults are used elsewhere because falsy-but-present is fine.
* ``from_dict`` is *defensive* — unknown keys are silently dropped,
  type errors fall back to defaults. Validation runs separately in
  ``validator.py``. This split lets the UI render half-broken configs
  with field-level errors instead of refusing to load.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums — string-valued so YAML serialisation is a no-op.
# ---------------------------------------------------------------------------


class Strategy(str, Enum):
    SUBDOMAIN = "subdomain"
    PATH = "path"
    HYBRID = "hybrid"


class Binding(str, Enum):
    AUTO = "auto"
    K8S_INGRESS = "k8s_ingress"
    K8S_LOADBALANCER = "k8s_loadbalancer"
    COMPOSE_HOST_PORT = "compose_host_port"
    COMPOSE_LOOPBACK = "compose_loopback"


class AuthGate(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NONE = "none"


class CertSource(str, Enum):
    CERT_MANAGER = "cert_manager"
    ACME_DIRECT = "acme_direct"
    UPLOADED = "uploaded"
    CLOUDFLARE_ORIGIN = "cloudflare_origin"


class CertStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class IssuerKind(str, Enum):
    ISSUER = "Issuer"
    CLUSTER_ISSUER = "ClusterIssuer"


class AcmeChallenge(str, Enum):
    HTTP01 = "http01"
    DNS01 = "dns01"


class ApexAction(str, Enum):
    # ``NONE`` is the migration-safe default: emit no apex rule, let
    # the catch-all (or path routes) handle bare-hostname requests.
    # v1 had no explicit apex handling, so migrated configs land here.
    NONE = "none"
    REDIRECT = "redirect"
    STATIC = "static"
    SERVICE = "service"


class CatchAllAction(str, Enum):
    NOT_FOUND = "404"
    REDIRECT = "redirect"
    BLOCK = "block"
    SERVICE = "service"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_enum(enum_cls: type[Enum], value: Any, default: Enum) -> Enum:
    """Coerce a string/enum to ``enum_cls``; fall back to ``default`` when
    the value isn't a valid member. Defensive on purpose — schema
    parsing must not raise on bad data; the validator surfaces issues
    field-by-field instead."""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            return default
    return default


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if v is not None]


def _enum_to_str(value: Any) -> Any:
    """Recursively convert Enum values to their .value for serialisation."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _enum_to_str(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_enum_to_str(v) for v in value]
    return value


def _dc_to_dict(obj: Any) -> dict[str, Any]:
    """Dataclass → dict, dropping ``None`` for optional sub-dataclasses
    so YAML stays tidy. Enum values become their string ``.value``."""
    if not is_dataclass(obj):
        raise TypeError(f"_dc_to_dict expects a dataclass, got {type(obj).__name__}")
    out: dict[str, Any] = {}
    for f in fields(obj):
        val = getattr(obj, f.name)
        if val is None:
            continue
        if is_dataclass(val):
            out[f.name] = _dc_to_dict(val)
        elif isinstance(val, list) and val and is_dataclass(val[0]):
            out[f.name] = [_dc_to_dict(v) for v in val]
        else:
            out[f.name] = _enum_to_str(val)
    return out


# ---------------------------------------------------------------------------
# Sub-shapes
# ---------------------------------------------------------------------------


@dataclass
class ExposureConfig:
    enabled: bool = False
    binding: Binding = Binding.AUTO
    public_hostnames: list[str] = field(default_factory=list)
    bind_addresses: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Any) -> ExposureConfig:
        if not isinstance(d, dict):
            return cls()
        return cls(
            enabled=_coerce_bool(d.get("enabled"), False),
            binding=_coerce_enum(Binding, d.get("binding"), Binding.AUTO),  # type: ignore[arg-type]
            public_hostnames=_coerce_str_list(d.get("public_hostnames")),
            bind_addresses=_coerce_str_list(d.get("bind_addresses")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class HostHeaders:
    response_set: dict[str, str] = field(default_factory=dict)
    response_remove: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Any) -> HostHeaders | None:
        if not isinstance(d, dict):
            return None
        return cls(
            response_set={
                str(k): str(v)
                for k, v in (d.get("response_set") or {}).items()
            },
            response_remove=_coerce_str_list(d.get("response_remove")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class HostAuth:
    gate: AuthGate = AuthGate.NONE
    provider: str = ""

    @classmethod
    def from_dict(cls, d: Any) -> HostAuth | None:
        if not isinstance(d, dict):
            return None
        return cls(
            gate=_coerce_enum(AuthGate, d.get("gate"), AuthGate.NONE),  # type: ignore[arg-type]
            provider=_coerce_str(d.get("provider")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class HostTls:
    cert_id: str = ""
    force_https: bool = True

    @classmethod
    def from_dict(cls, d: Any) -> HostTls | None:
        if not isinstance(d, dict):
            return None
        return cls(
            cert_id=_coerce_str(d.get("cert_id")),
            force_https=_coerce_bool(d.get("force_https"), True),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class HostRateLimit:
    per_second: int = 0
    burst: int = 0

    @classmethod
    def from_dict(cls, d: Any) -> HostRateLimit | None:
        if not isinstance(d, dict):
            return None
        return cls(
            per_second=_coerce_int(d.get("per_second")),
            burst=_coerce_int(d.get("burst")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class HostGeoAcl:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Any) -> HostGeoAcl | None:
        if not isinstance(d, dict):
            return None
        return cls(
            allow=_coerce_str_list(d.get("allow")),
            deny=_coerce_str_list(d.get("deny")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class HostEntry:
    role: str = ""
    service_id: str = ""
    canonical: str = ""
    aliases: list[str] = field(default_factory=list)
    path_prefix: str = ""
    tls: HostTls | None = None
    auth: HostAuth | None = None
    websocket: bool = False
    timeout_seconds: int = 0
    body_limit_mb: int = 0
    headers: HostHeaders | None = None
    rate_limit: HostRateLimit | None = None
    geo_acl: HostGeoAcl | None = None
    maintenance: bool = False

    @classmethod
    def from_dict(cls, d: Any) -> HostEntry:
        if not isinstance(d, dict):
            return cls()
        return cls(
            role=_coerce_str(d.get("role")),
            service_id=_coerce_str(d.get("service_id")),
            canonical=_coerce_str(d.get("canonical")),
            aliases=_coerce_str_list(d.get("aliases")),
            path_prefix=_coerce_str(d.get("path_prefix")),
            tls=HostTls.from_dict(d.get("tls")),
            auth=HostAuth.from_dict(d.get("auth")),
            websocket=_coerce_bool(d.get("websocket"), False),
            timeout_seconds=_coerce_int(d.get("timeout_seconds")),
            body_limit_mb=_coerce_int(d.get("body_limit_mb")),
            headers=HostHeaders.from_dict(d.get("headers")),
            rate_limit=HostRateLimit.from_dict(d.get("rate_limit")),
            geo_acl=HostGeoAcl.from_dict(d.get("geo_acl")),
            maintenance=_coerce_bool(d.get("maintenance"), False),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class PathAlias:
    from_path: str = ""
    to_path: str = ""
    code: int = 301

    @classmethod
    def from_dict(cls, d: Any) -> PathAlias:
        if not isinstance(d, dict):
            return cls()
        # Accept both ``from``/``to`` (YAML-friendly) and
        # ``from_path``/``to_path`` (Python-keyword-safe). The wire
        # form uses ``from``/``to``; Python uses ``_path`` because
        # ``from`` is a reserved word.
        return cls(
            from_path=_coerce_str(d.get("from") or d.get("from_path")),
            to_path=_coerce_str(d.get("to") or d.get("to_path")),
            code=_coerce_int(d.get("code"), 301),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_path,
            "to": self.to_path,
            "code": self.code,
        }


@dataclass
class ApexConfig:
    action: ApexAction = ApexAction.NONE
    target: str = ""
    code: int = 302

    @classmethod
    def from_dict(cls, d: Any) -> ApexConfig:
        if not isinstance(d, dict):
            return cls()
        return cls(
            action=_coerce_enum(ApexAction, d.get("action"), ApexAction.NONE),  # type: ignore[arg-type]
            target=_coerce_str(d.get("target")),
            code=_coerce_int(d.get("code"), 302),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class CatchAllConfig:
    action: CatchAllAction = CatchAllAction.NOT_FOUND
    target: str = ""
    code: int = 302
    custom_404_body: str = ""

    @classmethod
    def from_dict(cls, d: Any) -> CatchAllConfig:
        if not isinstance(d, dict):
            return cls()
        return cls(
            action=_coerce_enum(CatchAllAction, d.get("action"), CatchAllAction.NOT_FOUND),  # type: ignore[arg-type]
            target=_coerce_str(d.get("target")),
            code=_coerce_int(d.get("code"), 302),
            custom_404_body=_coerce_str(d.get("custom_404_body")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class CertManagerSolver:
    provider: str = "manual"
    secret_ref: str = ""

    @classmethod
    def from_dict(cls, d: Any) -> CertManagerSolver:
        if not isinstance(d, dict):
            return cls()
        return cls(
            provider=_coerce_str(d.get("provider"), "manual"),
            secret_ref=_coerce_str(d.get("secret_ref")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class CertManagerConfig:
    issuer_kind: IssuerKind = IssuerKind.CLUSTER_ISSUER
    issuer_name: str = ""
    challenge: AcmeChallenge = AcmeChallenge.HTTP01
    solver: CertManagerSolver = field(default_factory=CertManagerSolver)
    secret_name: str = ""

    @classmethod
    def from_dict(cls, d: Any) -> CertManagerConfig | None:
        if not isinstance(d, dict):
            return None
        return cls(
            issuer_kind=_coerce_enum(IssuerKind, d.get("issuer_kind"), IssuerKind.CLUSTER_ISSUER),  # type: ignore[arg-type]
            issuer_name=_coerce_str(d.get("issuer_name")),
            challenge=_coerce_enum(AcmeChallenge, d.get("challenge"), AcmeChallenge.HTTP01),  # type: ignore[arg-type]
            solver=CertManagerSolver.from_dict(d.get("solver") or {}),
            secret_name=_coerce_str(d.get("secret_name")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class AcmeDirectConfig:
    directory_url: str = "https://acme-v02.api.letsencrypt.org/directory"
    email: str = ""
    challenge: AcmeChallenge = AcmeChallenge.HTTP01

    @classmethod
    def from_dict(cls, d: Any) -> AcmeDirectConfig | None:
        if not isinstance(d, dict):
            return None
        return cls(
            directory_url=_coerce_str(
                d.get("directory_url"),
                "https://acme-v02.api.letsencrypt.org/directory",
            ),
            email=_coerce_str(d.get("email")),
            challenge=_coerce_enum(AcmeChallenge, d.get("challenge"), AcmeChallenge.HTTP01),  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class CertEntry:
    id: str = ""
    source: CertSource = CertSource.UPLOADED
    common_name: str = ""
    sans: list[str] = field(default_factory=list)
    cert_manager: CertManagerConfig | None = None
    acme_direct: AcmeDirectConfig | None = None
    expires_at: str = ""           # populated by controller, read-only
    last_renewed_at: str = ""      # populated by controller, read-only
    auto_renew: bool = True
    status: CertStatus = CertStatus.PENDING
    failure_message: str = ""

    @classmethod
    def from_dict(cls, d: Any) -> CertEntry:
        if not isinstance(d, dict):
            return cls()
        return cls(
            id=_coerce_str(d.get("id")),
            source=_coerce_enum(CertSource, d.get("source"), CertSource.UPLOADED),  # type: ignore[arg-type]
            common_name=_coerce_str(d.get("common_name")),
            sans=_coerce_str_list(d.get("sans")),
            cert_manager=CertManagerConfig.from_dict(d.get("cert_manager")),
            acme_direct=AcmeDirectConfig.from_dict(d.get("acme_direct")),
            expires_at=_coerce_str(d.get("expires_at")),
            last_renewed_at=_coerce_str(d.get("last_renewed_at")),
            auto_renew=_coerce_bool(d.get("auto_renew"), True),
            status=_coerce_enum(CertStatus, d.get("status"), CertStatus.PENDING),  # type: ignore[arg-type]
            failure_message=_coerce_str(d.get("failure_message")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


@dataclass
class RoutingDefaults:
    websocket: bool = False
    auth: HostAuth | None = None
    timeout_seconds: int = 60
    body_limit_mb: int = 100
    headers: HostHeaders | None = None

    @classmethod
    def from_dict(cls, d: Any) -> RoutingDefaults:
        if not isinstance(d, dict):
            return cls()
        return cls(
            websocket=_coerce_bool(d.get("websocket"), False),
            auth=HostAuth.from_dict(d.get("auth")),
            timeout_seconds=_coerce_int(d.get("timeout_seconds"), 60),
            body_limit_mb=_coerce_int(d.get("body_limit_mb"), 100),
            headers=HostHeaders.from_dict(d.get("headers")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dc_to_dict(self)


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


@dataclass
class RoutingConfigV2:
    version: int = 2
    base_domain: str = "local"
    stack_subdomain: str = "media-stack"
    gateway_host: str = "apps.media-stack.local"
    gateway_port: int = 80
    strategy: Strategy = Strategy.HYBRID
    scheme: str = ""
    app_path_prefix: str = "/app"
    exposure: ExposureConfig = field(default_factory=ExposureConfig)
    hosts: list[HostEntry] = field(default_factory=list)
    path_aliases: list[PathAlias] = field(default_factory=list)
    apex: ApexConfig = field(default_factory=ApexConfig)
    catch_all: CatchAllConfig = field(default_factory=CatchAllConfig)
    certs: list[CertEntry] = field(default_factory=list)
    defaults: RoutingDefaults = field(default_factory=RoutingDefaults)

    @classmethod
    def from_dict(cls, d: Any) -> RoutingConfigV2:
        if not isinstance(d, dict):
            return cls()
        return cls(
            version=_coerce_int(d.get("version"), 2),
            base_domain=_coerce_str(d.get("base_domain"), "local"),
            stack_subdomain=_coerce_str(d.get("stack_subdomain"), "media-stack"),
            gateway_host=_coerce_str(d.get("gateway_host"), "apps.media-stack.local"),
            gateway_port=_coerce_int(d.get("gateway_port"), 80),
            strategy=_coerce_enum(Strategy, d.get("strategy"), Strategy.HYBRID),  # type: ignore[arg-type]
            scheme=_coerce_str(d.get("scheme")),
            app_path_prefix=_coerce_str(d.get("app_path_prefix"), "/app"),
            exposure=ExposureConfig.from_dict(d.get("exposure") or {}),
            hosts=[HostEntry.from_dict(h) for h in (d.get("hosts") or [])],
            path_aliases=[PathAlias.from_dict(p) for p in (d.get("path_aliases") or [])],
            apex=ApexConfig.from_dict(d.get("apex") or {}),
            catch_all=CatchAllConfig.from_dict(d.get("catch_all") or {}),
            certs=[CertEntry.from_dict(c) for c in (d.get("certs") or [])],
            defaults=RoutingDefaults.from_dict(d.get("defaults") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        # PathAlias has wire-name remapping (from_path → from); keep the
        # custom serialisation by going through its own to_dict rather
        # than the generic dataclass walker.
        out: dict[str, Any] = {
            "version": self.version,
            "base_domain": self.base_domain,
            "stack_subdomain": self.stack_subdomain,
            "gateway_host": self.gateway_host,
            "gateway_port": self.gateway_port,
            "strategy": self.strategy.value,
            "scheme": self.scheme,
            "app_path_prefix": self.app_path_prefix,
            "exposure": self.exposure.to_dict(),
            "hosts": [h.to_dict() for h in self.hosts],
            "path_aliases": [p.to_dict() for p in self.path_aliases],
            "apex": self.apex.to_dict(),
            "catch_all": self.catch_all.to_dict(),
            "certs": [c.to_dict() for c in self.certs],
            "defaults": self.defaults.to_dict(),
        }
        return out
