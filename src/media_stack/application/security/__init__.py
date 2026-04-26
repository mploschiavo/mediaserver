"""Security application layer — cross-provider session and API-token
aggregators plus the admin security-report service.

ADR-0002 Phase 16-E (cross-cutting security) — the orchestration
half of the security subsystem. The pure value-object DTOs ride
along with their orchestrators here (``SessionDTO``,
``APITokenRecord``, the report alert dataclasses) — they are
public-API shapes returned by use cases, not standalone
domain-layer entities, so co-locating them with the use case is
the path of least surprise. ``domain/security/`` stays empty for
now; if a future caller needs a pure-data type in isolation we'll
extract it then.

Importing this package re-exports the aggregator + service classes
plus their public DTOs. The legacy ``services.security`` package
shim at the old import path re-exports from here.
"""

from __future__ import annotations

from .api_token_aggregator import (
    APITokenAggregator,
    APITokenRecord,
    CONTROLLER_PROVIDER as API_TOKEN_CONTROLLER_PROVIDER,
    ControllerTokenStoreProtocol,
)
from .session_aggregator import (
    CONTROLLER_PROVIDER,
    SessionAggregator,
    SessionDTO,
    SessionStoreProtocol,
)
from .security_report_service import (
    ConcurrentSessionAlert,
    FailedLoginCluster,
    NewLocationAlert,
    SecurityReportService,
)

__all__ = [
    "APITokenAggregator",
    "APITokenRecord",
    "API_TOKEN_CONTROLLER_PROVIDER",
    "CONTROLLER_PROVIDER",
    "ConcurrentSessionAlert",
    "ControllerTokenStoreProtocol",
    "FailedLoginCluster",
    "NewLocationAlert",
    "SecurityReportService",
    "SessionAggregator",
    "SessionDTO",
    "SessionStoreProtocol",
]
