"""BootConfigureAuthService — synchronous Authelia config write before the API server opens.

ADR-0015 Phase 7e. Pre-Phase-7e the ``_run_boot_configure_auth``
method (+ its ``_BootCtxShim`` ctx assembler) lived on
``ControllerServeCommand`` in commands/. The behaviour is
load-bearing: when ``profile.auth.provider`` is authelia(+oidc),
the Authelia container waits on the controller's health endpoint
and starts the moment the API returns 200. If it reads
placeholder secrets from the bootstrap defaults it encrypts
``db.sqlite3`` with those placeholders, and the real secrets that
configure-auth later emits become unable to decrypt the rows —
the recurring crashloop we kept hitting pre-v1.0.140.

Running configure-auth here makes the first Authelia boot use
real secrets on its very first write, closing that window. The
service is constructed once at controller boot; ``run`` is
called synchronously before ``start_api_server`` opens the
listening socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from media_stack.cli.workflows.controller_boot.boot_profile_loader import (
    BootProfileLoader,
)
from media_stack.core.auth.configure_auth_job import (
    configure_auth as _CONFIGURE_AUTH_FN,
)


@dataclass
class _BootCtxShim:
    """Minimal ctx object for the configure-auth job — same attributes
    the dispatcher would assemble, without pulling in the whole
    action pipeline just to run one sync step at boot."""

    profile: dict
    config_root: str
    admin_username: str


class BootConfigureAuthService:
    """Synchronous Authelia config write before the API server opens."""

    def __init__(
        self,
        profile_loader: BootProfileLoader,
        log: Callable[[str], None],
    ) -> None:
        self._profile_loader = profile_loader
        self._log = log

    def run(self, env: dict) -> None:
        """Write the Authelia config before the API server opens.

        Fail-open: if anything goes wrong we log a warning and proceed —
        the API server still starts and configure-auth will be retried
        on the first bootstrap action.
        """
        try:
            profile = self._profile_loader.load(env)
            auth_cfg = profile.get("auth") or {}
            provider = str(auth_cfg.get("provider", "") or "").strip().lower()
            if provider not in ("authelia", "authelia+oidc"):
                return
            ctx = _BootCtxShim(
                profile=profile,
                config_root=env.get("CONFIG_ROOT", "/srv-config"),
                admin_username=env.get("STACK_ADMIN_USERNAME", "admin"),
            )
            result = _CONFIGURE_AUTH_FN(ctx)
            if result.get("error"):
                self._log(f"[WARN] boot configure-auth: {result['error']}")
            else:
                self._log(
                    "[OK] boot configure-auth: Authelia config "
                    "sealed before API server opened",
                )
        except Exception as exc:  # noqa: BLE001 — fail-open boot path
            # Configure-auth raises a wide variety of exception types
            # (kubernetes errors, yaml errors, file IO, subprocess
            # failures from chmod/chown). Catching broadly here is
            # deliberate: the boot path must continue even if this
            # subsystem fails; we log + move on.
            self._log(f"[WARN] boot configure-auth raised: {exc}")


__all__ = ["BootConfigureAuthService", "_BootCtxShim"]
