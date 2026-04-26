"""Routing v2 schema, migrator, and validator.

Three modules sit under this package — each is purposefully isolated:

* ``schema_v2``  — pure dataclasses + enums + ``from_dict``/``to_dict``
  helpers. No I/O, no validation logic. The shape an operator sees in
  the persisted ``routing.yaml`` lives here.
* ``migrator``   — converts the legacy v1 routing dict (``direct_hosts``
  as a flat ``{role: host}`` dict + a handful of top-level fields)
  into a ``RoutingConfigV2``. Pure function; backwards-compat path.
* ``validator``  — enforces the rules in the design doc (VR-1 .. VR-11)
  against a ``RoutingConfigV2``. Returns ``[ValidationError]`` so the
  UI can mark the offending fields.

Wired into the API in PR-2 (handlers_get/post). PR-1 lands the data
layer only — no behavior changes.
"""

from .schema_v2 import (
    AcmeDirectConfig,
    ApexAction,
    ApexConfig,
    AuthGate,
    Binding,
    CatchAllAction,
    CatchAllConfig,
    CertEntry,
    CertManagerConfig,
    CertManagerSolver,
    CertSource,
    ExposureConfig,
    HostAuth,
    HostEntry,
    HostGeoAcl,
    HostHeaders,
    HostRateLimit,
    HostTls,
    PathAlias,
    RoutingConfigV2,
    RoutingDefaults,
    Strategy,
)
from .migrator import migrate_v1_to_v2
from .validator import (
    ValidationError,
    validate_routing_config,
)

__all__ = [
    "AcmeDirectConfig",
    "ApexAction",
    "ApexConfig",
    "AuthGate",
    "Binding",
    "CatchAllAction",
    "CatchAllConfig",
    "CertEntry",
    "CertManagerConfig",
    "CertManagerSolver",
    "CertSource",
    "ExposureConfig",
    "HostAuth",
    "HostEntry",
    "HostGeoAcl",
    "HostHeaders",
    "HostRateLimit",
    "HostTls",
    "PathAlias",
    "RoutingConfigV2",
    "RoutingDefaults",
    "Strategy",
    "ValidationError",
    "migrate_v1_to_v2",
    "validate_routing_config",
]
