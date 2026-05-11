"""``cli/workflows/controller_boot/`` — controller boot-time services.

ADR-0015 Phase 7e. Pre-Phase-7e the controller's boot sequence
(API-key canary validation + pre-API Authelia config write) lived
inline inside the 683-LoC ``ControllerServeCommand._run_serve``
god method. Phase 7e extracts each boot-time concern onto its own
SRP class:

* :class:`KeyCanaryValidator` (Strategy) — probe a discovered API
  key against its running service to detect config-mount mismatches.
* :class:`BootProfileLoader` (Repository) — best-effort load of
  the boot-time bootstrap profile YAML.
* :class:`BootConfigureAuthService` (Service) — synchronous
  Authelia config write before the API server opens, closing
  the placeholder-secret window that caused the recurring
  db.sqlite3 decryption-failure crashloop pre-v1.0.140.
"""

from media_stack.cli.workflows.controller_boot.boot_configure_auth_service import (
    BootConfigureAuthService,
)
from media_stack.cli.workflows.controller_boot.boot_profile_loader import (
    BootProfileLoader,
)
from media_stack.cli.workflows.controller_boot.key_canary_validator import (
    KeyCanaryValidator,
)


__all__ = [
    "BootConfigureAuthService",
    "BootProfileLoader",
    "KeyCanaryValidator",
]
