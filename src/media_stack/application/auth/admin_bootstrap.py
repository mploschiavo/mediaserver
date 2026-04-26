"""Admin bootstrap — seed the controller's user store with an admin
account on first run so the Users tab isn't empty after a fresh deploy
and so the ``STACK_ADMIN_PASSWORD`` env var can eventually be phased
out as a permanent auth override.

Today's behaviour (Phase 1):

- If the store already has an active superadmin, do nothing.
- Otherwise, if ``STACK_ADMIN_PASSWORD`` is set, create an admin row
  with ``source="env-seed"`` using the env credential. The row is
  also provisioned into Authelia via the normal source-of-truth
  path so basic-auth login works both ways.
- Otherwise, do nothing yet — Phase 3 will generate a random password
  and write it to ``.initial-admin-password``.

The env-fallback verifier stays on for Phase 1: Phase 2 will remove
it once the seed flow is trusted. That keeps this change safe to
roll out — worst case we seed a redundant admin row, not lock
anyone out.

See project memory ``Admin bootstrap redesign`` for the full plan.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from media_stack.domain.auth.users.models import UserState
from media_stack.application.auth.users.user_service import UserService
from media_stack.application.auth.users.user_service_base import UserServiceError

_log = logging.getLogger("media_stack")

_DEFAULT_ADMIN_USERNAME = "admin"
_DEFAULT_ADMIN_EMAIL_SUFFIX = "@local"
_DEFAULT_DISPLAY_NAME = "Administrator"
_DEFAULT_ROLE_SLUG = "superadmin"
_ERR_MSG_TRUNCATE = 99

# Known-weak passwords we refuse to boot with when the deployment
# is marked internet_exposed. The default "admin" belongs here
# specifically so the convenience default can't accidentally ship
# to the public internet — any deploy-time override must clear the
# blocklist before the controller will start.
_WEAK_PASSWORD_BLOCKLIST = frozenset({
    "admin", "administrator", "password", "passw0rd", "changeme",
    "letmein", "media-stack", "root", "toor", "default", "12345",
    "123456", "1234567", "12345678", "qwerty", "welcome",
})


class AdminBootstrapWeakPasswordError(RuntimeError):
    """Raised when internet_exposed=true and the admin credential
    is still on the well-known default blocklist. The controller
    refuses to come up so the stack can't get scanned and owned
    with an already-published credential."""


class AdminBootstrap:
    """One-shot seeder. Idempotent — calling ``run`` repeatedly is safe
    because the 'any active superadmin?' check short-circuits every
    call after the first."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self._env = dict(env) if env is not None else dict(os.environ)

    # Class-level accessor so callers can catch the fatal-
    # blocklist case without importing the exception separately.
    WeakPasswordError = AdminBootstrapWeakPasswordError

    def run(
        self, service: UserService, *, internet_exposed: bool = False,
    ) -> dict[str, Any]:
        """Seed admin if the store has no active superadmin.

        Returns a dict describing what happened, suitable for logging
        or exposing via a /admin/bootstrap status endpoint later:

          - {"action": "skipped", "reason": "existing_superadmin"}
          - {"action": "seeded", "source": "env-seed", ...}
          - {"action": "linked", "source": "env-legacy", ...}
          - {"action": "skipped", "reason": "no_credential"}
          - {"action": "error", "error": "..."}

        ``internet_exposed`` flips the blocklist check from warn to
        fatal: when True, a weak env password raises so the stack
        refuses to boot instead of shipping a well-known credential
        to the public internet.
        """
        if self._has_active_superadmin(service):
            return {"action": "skipped", "reason": "existing_superadmin"}
        password = self._env.get("STACK_ADMIN_PASSWORD", "").strip()
        if not password:
            return {"action": "skipped", "reason": "no_credential"}
        self._check_blocklist(password, internet_exposed)
        username, email = self._resolve_identity()
        # If the source-of-truth already has an admin row (a deploy
        # from before Phase 1 wrote users_database.yml but never a
        # users.json), link that row into the store instead of
        # re-creating — re-creating would collide on the provider
        # side and fail the whole seed.
        legacy = self._find_existing_sot_admin(service, username)
        if legacy is not None:
            return self._link_legacy_admin(
                service, username, email, legacy, password,
            )
        return self._seed_fresh_admin(service, username, email, password)

    def _seed_fresh_admin(
        self, service: UserService, username: str, email: str, password: str,
    ) -> dict[str, Any]:
        try:
            # skip_policy_check: STACK_ADMIN_PASSWORD is a bootstrap
            # credential that may be weak ("media-stack" default).
            # Policy enforcement moves to forced-rotation (Phase 3).
            result = service.create_user(
                email=email,
                username=username,
                display_name=_DEFAULT_DISPLAY_NAME,
                role_slug=_DEFAULT_ROLE_SLUG,
                password=password,
                actor="admin-bootstrap",
                skip_policy_check=True,
            )
        except UserServiceError as exc:
            _log.warning(
                "[WARN] admin-bootstrap: could not seed admin: %s", exc,
            )
            return {"action": "error",
                    "error": str(exc)[:_ERR_MSG_TRUNCATE]}
        user_id = result.get("id") or ""
        self._tag_source(service, user_id, "env-seed")
        _log.info(
            "[OK] admin-bootstrap: seeded admin user %r from "
            "STACK_ADMIN_PASSWORD (source=env-seed)", username,
        )
        return {"action": "seeded", "source": "env-seed",
                "user_id": user_id, "username": username}

    def _link_legacy_admin(
        self, service: UserService, username: str, email: str,
        external_id: str, env_password: str,
    ) -> dict[str, Any]:
        """Adopt an admin row that already exists in the source-of-
        truth but isn't yet in the controller store.

        Also seeds the provider's password from the env value when
        the existing row has no password set — covers the fresh-
        install case where the /defaults users_database.yml ships
        with just a username (no hash) so a fresh deploy doesn't
        ship every install with a well-known credential. Once the
        admin rotates, the env value stops working (Phase 2)."""
        source_provider = service._source_of_truth()
        provider_name = source_provider.name if source_provider else "authelia"
        try:
            user = service._store.create(
                email=email,
                username=username,
                display_name=_DEFAULT_DISPLAY_NAME,
                role_slug=_DEFAULT_ROLE_SLUG,
                source="env-legacy",
            )
            service._store.update(
                user.id,
                provider_refs={provider_name: external_id},
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "[WARN] admin-bootstrap: could not link legacy admin: %s",
                exc,
            )
            return {"action": "error",
                    "error": str(exc)[:_ERR_MSG_TRUNCATE]}
        password_seeded = self._ensure_provider_password(
            source_provider, external_id, env_password,
        )
        _log.info(
            "[OK] admin-bootstrap: linked existing %s admin %r into "
            "the controller store (source=env-legacy%s)",
            provider_name, username,
            ", password seeded" if password_seeded else "",
        )
        return {"action": "linked", "source": "env-legacy",
                "user_id": user.id, "username": username,
                "password_seeded": password_seeded}

    def _ensure_provider_password(
        self, provider: Any, external_id: str, env_password: str,
    ) -> bool:
        """Write env_password to the provider iff the existing row
        has no password. Returns True if a write happened.

        This is the self-healing bit for fresh installs where
        users_database.yml was seeded without a password. Without
        it, Authelia crashloops with 'Users.admin.users: non zero
        value required' forever until the operator hand-edits
        users_database.yml. The write uses set_password so Authelia's
        SafeYamlEditor does an atomic rename + backup — the same
        path every dashboard password change goes through."""
        if provider is None or not env_password:
            return False
        has_password = self._provider_row_has_password(provider, external_id)
        if has_password:
            return False
        try:
            provider.set_password(external_id, env_password)
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] admin-bootstrap: could not seed provider "
                "password: %s", exc,
            )
            return False
        return True

    def _provider_row_has_password(
        self, provider: Any, external_id: str,
    ) -> bool:
        """Ask the provider whether the matching row has a password
        set. Providers are expected to expose a ``has_password``
        boolean in ExternalUser.extra (never the hash itself) —
        see media_stack/services/apps/authelia/user_provider.py.

        When a provider doesn't expose the signal we return True so
        the bootstrap errs on the side of NOT overwriting an
        operator-set credential we can't see."""
        try:
            for ext_user in provider.list_users():
                if str(ext_user.external_id) != external_id:
                    continue
                extra = getattr(ext_user, "extra", {}) or {}
                if "has_password" in extra:
                    return bool(extra.get("has_password"))
                # Safe default: assume password is set unless told
                # otherwise. Prevents destructive re-seed when the
                # provider can't report the flag.
                return True
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] admin-bootstrap: could not inspect "
                "provider row: %s", exc,
            )
        return False

    def _find_existing_sot_admin(
        self, service: UserService, username: str,
    ) -> str | None:
        """Return the external_id of a source-of-truth admin whose
        username/external_id matches, or None."""
        source = service._source_of_truth()
        if source is None:
            return None
        try:
            for ext_user in source.list_users():
                if str(ext_user.username).lower() == username.lower():
                    return str(ext_user.external_id)
                if str(ext_user.external_id).lower() == username.lower():
                    return str(ext_user.external_id)
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] admin-bootstrap: could not list sot users: %s",
                exc,
            )
        return None

    def _resolve_identity(self) -> tuple[str, str]:
        """Pick the admin username and email from env with sane
        defaults. Kept separate so ``run`` stays under the
        methods-over-50-lines ratchet."""
        username = self._env.get(
            "STACK_ADMIN_USERNAME", _DEFAULT_ADMIN_USERNAME,
        ).strip() or _DEFAULT_ADMIN_USERNAME
        email = self._env.get("STACK_ADMIN_EMAIL", "").strip() \
            or f"{username}{_DEFAULT_ADMIN_EMAIL_SUFFIX}"
        return username, email

    def _tag_source(
        self, service: UserService, user_id: str, source: str,
    ) -> None:
        """Mark the freshly-created admin row with ``source`` so the
        UI can surface a badge and Phase 2 can decide whether the
        env-fallback path is still active."""
        if not user_id:
            return
        try:
            service._store.update(user_id, source=source)
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] admin-bootstrap: could not tag source: %s", exc,
            )

    def _check_blocklist(
        self, password: str, internet_exposed: bool,
    ) -> None:
        """Warn (or refuse to boot) when STACK_ADMIN_PASSWORD is
        on the well-known weak-password list.

        Local LAN deploys just get a warning — the forced rotation
        flow is expected to close the window within one sign-in.
        Internet-exposed deploys raise because a scanner can hit
        the login endpoint within seconds of the service coming
        up; even one minute of ``admin``/``admin`` is too much."""
        if password.lower() not in _WEAK_PASSWORD_BLOCKLIST:
            return
        if internet_exposed:
            raise AdminBootstrapWeakPasswordError(
                f"STACK_ADMIN_PASSWORD={password!r} is a well-known "
                "weak credential and profile.internet_exposed=true. "
                "Set STACK_ADMIN_PASSWORD to something that isn't in "
                "the default blocklist before starting the stack.",
            )
        _log.warning(
            "[WARN] admin-bootstrap: STACK_ADMIN_PASSWORD is a "
            "well-known weak credential. The forced-rotation flow "
            "will close the window on first login. Set "
            "internet_exposed=true in the profile to make this "
            "fatal.",
        )

    def _has_active_superadmin(self, service: UserService) -> bool:
        """True if any active user has role_slug=superadmin. Walks the
        store directly so the result is independent of whichever
        provider the service is configured with."""
        try:
            users = service._store.list_all(include_deleted=False)
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] admin-bootstrap: list_all failed (%s), assuming "
                "store is empty", exc,
            )
            return False
        for user in users:
            if user.role_slug != _DEFAULT_ROLE_SLUG:
                continue
            if user.state != UserState.ACTIVE:
                continue
            return True
        return False
