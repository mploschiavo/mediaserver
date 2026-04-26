"""Off-request propagation of a password change to service admins.

Split out of user_write_service.py so that file stays under the
400-line ratchet without losing the behaviour: ``reset_password``
returns as soon as the source-of-truth provider (Authelia) is
updated, and the slower per-app admin-password rotations (Sonarr,
Radarr, qBittorrent, …) run in a daemon thread.

Errors from the background path land in the audit log under the
``reset_password.bg`` action so the operator can see what happened
without blocking the sign-in flow.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

_log = logging.getLogger("media_stack")

_ERR_LEN = 99


class ServiceAdminPropagator:
    """Run the per-adapter password rotation off the request thread.

    Injected into ``UserWriteService`` — tests swap the ``propagate``
    callable with a sync stub so the background work is deterministic
    under test.
    """

    def __init__(self, propagate: Callable[[str], dict[str, Any]], audit):
        self._propagate = propagate
        self._audit = audit

    def start(self, *, password: str, user_id: str,
              user_email: str, actor: str) -> None:
        threading.Thread(
            target=self._run,
            args=(password, user_id, user_email, actor),
            daemon=True,
            name=f"svc-admin-propagate-{user_id[:8]}",
        ).start()

    def _run(self, password: str, user_id: str,
             user_email: str, actor: str) -> None:
        try:
            results = self._propagate(password)
            failed = [
                name for name, status in (results or {}).items()
                if isinstance(status, str) and status.startswith("error:")
            ]
            if failed:
                self._audit.append(
                    actor=actor, action="reset_password.bg",
                    target=user_email, result="partial",
                    detail={
                        "user_id": user_id,
                        "service_admins": results,
                        "failed": failed,
                    },
                )
            else:
                _log.debug(
                    "[DEBUG] reset_password.bg: service-admins ok for %s",
                    user_email,
                )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "reset_password.bg: %s service-admin propagation raised: %s",
                user_email, exc,
            )
            try:
                self._audit.append(
                    actor=actor, action="reset_password.bg",
                    target=user_email, result="error",
                    detail={"user_id": user_id, "error": str(exc)[:_ERR_LEN]},
                )
            except Exception as audit_exc:  # noqa: BLE001
                _log.debug(
                    "[DEBUG] reset_password.bg audit append failed: %s",
                    audit_exc,
                )


def run_service_admin_propagation_async(
    propagate: Callable[[str], dict[str, Any]],
    audit,
    *,
    password: str,
    user_id: str,
    user_email: str,
    actor: str,
) -> None:
    """Module-level shim so callers can stay importing a function.
    The work itself lives on ``ServiceAdminPropagator`` so the
    class-structure ratchet sees a first-class citizen.
    """
    ServiceAdminPropagator(propagate, audit).start(
        password=password, user_id=user_id,
        user_email=user_email, actor=actor,
    )
