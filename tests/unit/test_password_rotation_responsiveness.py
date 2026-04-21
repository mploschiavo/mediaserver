"""Tests pinning the responsiveness fixes for the rotate-password
flow.

The 2026-04-20 user feedback: clicking "Rotate password" feels
unresponsive — the request takes ~1-2 seconds (argon2 hashing
chain + Authelia provider write) and the UI gives no visible
progress, so the user thinks the click didn't register.

Three fixes are pinned here:

1. **UI spinner** — the rotation modal must show "Rotating
   password…" with a spinner during the in-flight request.
   The user-tab reset flow must show a non-blocking toast.
2. **Boot pre-warm** — the controller must pre-warm argon2,
   the audit-chain hash cache, and the user-service singleton
   in a daemon thread at boot, so the FIRST rotation doesn't
   pay for one-time-per-process init.
3. **Background service-admin propagation** — the response
   returns as soon as the source-of-truth provider (Authelia)
   is updated. The downstream service-admin propagation
   (Sonarr/Radarr/qBittorrent/etc.) runs in a background
   thread so its slow HTTP calls don't pad the response time."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


# ----------------------------------------------------------------------
# 1. UI spinner pattern in dashboard.html
# ----------------------------------------------------------------------


DASHBOARD = (
    ROOT / "src" / "media_stack" / "api" / "dashboard.html"
).read_text(encoding="utf-8")


class RotationSpinnerUiTests(unittest.TestCase):

    def test_rotation_modal_uses_busy_helper(self) -> None:
        """The rotation modal in ``_enforceAdminRotation`` must
        toggle a busy state (visible label change + spinner)
        when the request is in flight, not just disable the
        button."""
        idx = DASHBOARD.find("function _enforceAdminRotation")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 6000]
        self.assertIn(
            "_setBusy(true)", body,
            "Rotation handler must call _setBusy(true) to show "
            "visible feedback during the in-flight request.",
        )
        self.assertIn(
            "Rotating password", body,
            "Busy state must change the button label so the user "
            "knows the click registered.",
        )
        self.assertIn(
            "ms-spinner", body,
            "A visible spinner element must be added during busy.",
        )
        self.assertIn(
            "_setBusy(false)", body,
            "Failure paths must un-busy the button; otherwise it "
            "stays stuck after a wrong-password attempt.",
        )

    def test_user_tab_reset_shows_toast_during_request(self) -> None:
        """The user-tab reset flow has all modals closed by the
        time the request fires; show a non-blocking toast so the
        user knows the click registered."""
        # Anchor the search at the reset-password fetch call so we
        # don't false-positive on an unrelated toast() elsewhere.
        idx = DASHBOARD.find("/api/users/${userId}/reset-password")
        self.assertGreater(idx, -1)
        # Look at the lines immediately before the fetch.
        window = DASHBOARD[max(0, idx - 600):idx]
        self.assertIn(
            "Resetting password", window,
            "User-tab reset must show a 'Resetting password…' "
            "toast so the user knows the click registered. "
            "Without it the UI looks frozen for ~1-2s.",
        )


# ----------------------------------------------------------------------
# 2. Boot pre-warm
# ----------------------------------------------------------------------


from media_stack.api.services import prewarm  # noqa: E402


class PrewarmTests(unittest.TestCase):

    def test_warm_argon2_does_not_raise(self) -> None:
        """Pre-warm runs in a daemon thread; it must never raise
        because errors there would either kill the thread silently
        or (worse) propagate up and crash the boot path."""
        prewarm.warm_argon2()
        prewarm.warm_basic_auth_verifier()
        # warm_user_service requires a user-service factory; it
        # safely no-ops without one.
        prewarm.warm_user_service()

    def test_run_in_background_returns_immediately(self) -> None:
        """The boot path must not block on pre-warm. Confirm the
        function returns in <100ms regardless of how slow the
        warmers actually are."""
        t0 = time.monotonic()
        prewarm.run_in_background()
        elapsed_ms = (time.monotonic() - t0) * 1000
        self.assertLess(
            elapsed_ms, 100,
            f"run_in_background blocked for {elapsed_ms:.0f}ms — "
            "boot path must not wait on the warmers.",
        )

    def test_warmer_thread_is_daemon(self) -> None:
        """A non-daemon warmer would hold the process open at
        shutdown if it got stuck (slow disk, missing file). Pin
        the daemon flag so a refactor can't accidentally make
        the controller un-killable.

        We patch threading.Thread to capture the daemon flag at
        creation time — checking after the fact races against the
        warmer finishing before our assertion runs."""
        captured: dict[str, bool] = {}
        original = threading.Thread

        def capture(*args, **kwargs):
            t = original(*args, **kwargs)
            if "prewarm" in str(kwargs.get("name", "")):
                captured["daemon"] = bool(kwargs.get("daemon", False))
                captured["name"] = kwargs.get("name", "")
            return t

        with mock.patch.object(prewarm.threading, "Thread", capture):
            prewarm.run_in_background()
        self.assertTrue(captured, "Prewarm thread was never created")
        self.assertTrue(
            captured["daemon"],
            f"{captured['name']} was created with daemon=False — "
            "a stuck warmer would block controller shutdown.",
        )


# ----------------------------------------------------------------------
# 3. Background service-admin propagation
# ----------------------------------------------------------------------


from media_stack.core.auth.users.user_write_service import (  # noqa: E402
    UserWriteService,
)


class _BlockingServiceAdminAdapter:
    """Test double that blocks for a configurable duration in
    ``set_admin_password``. Used to prove that the response
    doesn't wait on us."""

    def __init__(self, name: str, delay_seconds: float = 0.5) -> None:
        self.name = name
        self._delay = delay_seconds
        self.set_calls: list[str] = []

    def set_admin_password(self, password: str) -> None:
        time.sleep(self._delay)
        self.set_calls.append(password)


class _FastProvider:
    """In-memory provider that records set_password calls and
    returns immediately. Stands in for Authelia on the sync path."""

    def __init__(self) -> None:
        self.name = "fast"
        self.set_calls: list[str] = []

        class _Caps:
            supports_password = True

        self.capabilities = _Caps()

    def set_password(self, external_id: str, password: str) -> None:
        self.set_calls.append(password)


def _build_service_with(
    *,
    service_admins: list,
    propagate: bool = True,
) -> UserWriteService:
    """Build a minimal UserWriteService with stub collaborators.
    Uses MagicMock for the bits we don't exercise — keeps the
    test focused on the propagation timing."""
    svc = UserWriteService.__new__(UserWriteService)
    svc._store = mock.MagicMock()
    user = mock.MagicMock()
    user.id = "u-1"
    user.email = "u@example.com"
    user.password_history = []
    user.role_slug = "admin"
    user.provider_refs = {"fast": "external-1"}
    user.source = "rotated"
    svc._store.get.return_value = user
    svc._policy = mock.MagicMock()
    svc._policy.check_candidate.return_value = mock.MagicMock(ok=True)
    svc._policy.push_history.return_value = ["h1"]
    svc._providers = [_FastProvider()]
    role = mock.MagicMock()
    role.propagate_to_service_admins = propagate
    svc._roles = mock.MagicMock()
    svc._roles.get.return_value = role
    svc._service_admins = service_admins
    svc._audit = mock.MagicMock()
    return svc


class BackgroundServiceAdminPropagationTests(unittest.TestCase):

    def test_response_returns_before_service_admins_complete(self) -> None:
        """Service-admin propagation is the slow part. The
        response must return as soon as Authelia is sync'd —
        otherwise the rotation handler waits for every Sonarr/
        Radarr/qBit HTTP call to finish before the user sees
        success."""
        slow_admin = _BlockingServiceAdminAdapter("sonarr", delay_seconds=0.4)
        svc = _build_service_with(service_admins=[slow_admin])

        t0 = time.monotonic()
        result = svc.reset_password("u-1", password="abcd-1234-EFGH!", actor="admin")
        elapsed_ms = (time.monotonic() - t0) * 1000

        self.assertLess(
            elapsed_ms, 200,
            f"reset_password took {elapsed_ms:.0f}ms — service-admin "
            "propagation must not block the response.",
        )
        self.assertEqual(result["service_admins"], "scheduled_async")

    def test_background_thread_does_eventually_propagate(self) -> None:
        """The response returns fast, but the background thread
        must still push the new password to service admins."""
        slow_admin = _BlockingServiceAdminAdapter("sonarr", delay_seconds=0.05)
        svc = _build_service_with(service_admins=[slow_admin])

        svc.reset_password("u-1", password="abcd-1234-EFGH!", actor="admin")
        # Give the background thread time to run.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not slow_admin.set_calls:
            time.sleep(0.02)

        self.assertTrue(
            slow_admin.set_calls,
            "Background thread never propagated to the service admin "
            "— the rotation effectively didn't update Sonarr/Radarr.",
        )

    def test_background_failure_writes_audit_entry(self) -> None:
        """When a background propagation fails, the failure must
        land in the audit log so an operator can see what broke."""
        bad_admin = mock.MagicMock()
        bad_admin.name = "sonarr"
        bad_admin.set_admin_password.side_effect = RuntimeError("boom")
        svc = _build_service_with(service_admins=[bad_admin])

        svc.reset_password("u-1", password="abcd-1234-EFGH!", actor="admin")
        # Wait for the background thread to finish + audit.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            actions = [
                c.kwargs.get("action") for c in svc._audit.append.call_args_list
            ]
            if "reset_password.bg" in actions:
                break
            time.sleep(0.02)

        bg_calls = [
            c for c in svc._audit.append.call_args_list
            if c.kwargs.get("action") == "reset_password.bg"
        ]
        self.assertTrue(
            bg_calls,
            "Background failure didn't produce an audit entry. "
            "Operators won't see partial-failure state.",
        )

    def test_authelia_provider_call_is_synchronous(self) -> None:
        """The source-of-truth provider (Authelia) MUST run on the
        sync path — otherwise the user logs out, tries to sign in
        with the new password, and fails because Authelia hasn't
        been updated yet."""
        provider = _FastProvider()
        svc = UserWriteService.__new__(UserWriteService)
        svc._store = mock.MagicMock()
        user = mock.MagicMock()
        user.id = "u-1"
        user.email = "u@example.com"
        user.password_history = []
        user.role_slug = "admin"
        user.provider_refs = {"fast": "external-1"}
        user.source = "rotated"
        svc._store.get.return_value = user
        svc._policy = mock.MagicMock()
        svc._policy.check_candidate.return_value = mock.MagicMock(ok=True)
        svc._policy.push_history.return_value = ["h1"]
        svc._providers = [provider]
        role = mock.MagicMock()
        role.propagate_to_service_admins = False
        svc._roles = mock.MagicMock()
        svc._roles.get.return_value = role
        svc._service_admins = []
        svc._audit = mock.MagicMock()

        svc.reset_password("u-1", password="abcd-1234-EFGH!", actor="admin")
        # By the time reset_password returns, the provider must
        # already have been called — no background thread for
        # source-of-truth.
        self.assertEqual(
            provider.set_calls, ["abcd-1234-EFGH!"],
            "Provider was not called synchronously — Authelia "
            "wouldn't be updated when the user re-logs in.",
        )


if __name__ == "__main__":
    unittest.main()
