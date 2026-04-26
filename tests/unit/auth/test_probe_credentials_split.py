"""Tests for the split credential / password-propagation probes.

Two separated concerns that used to be tangled inside
``HealthService.probe_credentials``:

- ``probe_credentials`` now asks *"does the API key work?"* via a
  read-only GET — **no side effects** on the target service. Fixes
  the Jellyfin incident (2026-04-24) where every probe ticked a
  counter on the admin user row and fired HTTP 400s from an
  ``IncrementInvalidLoginAttemptCount`` concurrency race.
- ``probe_password_propagation`` is a distinct admin-triggered
  check that reads the user list and confirms the local
  ``HasPassword`` flag — no authentication attempt.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.health import HealthService  # noqa: E402


# Minimal fake service registry — matches the keys
# ``probe_credentials`` / ``probe_password_propagation`` reach for.
class _FakeService:
    def __init__(self, id, host="h", port=80, auth_path="/System/Info",
                 auth_mode="X-Emby-Token", login_path="/login",
                 login_mode="json_credentials", api_key_env="X_KEY"):
        self.id = id
        self.host = host
        self.port = port
        self.auth_path = auth_path
        self.auth_mode = auth_mode
        self.login_path = login_path
        self.login_mode = login_mode
        self.api_key_env = api_key_env


class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body if body is not None else b""

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ProbeCredentialsApiKeyPathTests(unittest.TestCase):
    """API-key services (Jellyfin, *arrs) now run a read-only GET."""

    def _patch_service_meta(self, services):
        return patch(
            "media_stack.api.services.health.SERVICES", services,
        )

    def _patch_login_probes(self, targets):
        # LOGIN_PROBES is derived from SERVICES at import; patch directly.
        return patch(
            "media_stack.api.services.health.LOGIN_PROBES", targets,
        )

    def test_api_key_service_returns_ok_on_2xx(self):
        jf = _FakeService(id="jellyfin")
        with self._patch_service_meta([jf]), self._patch_login_probes({
            "jellyfin": ("h", 80, "/login", "json_credentials"),
        }), patch.object(
            HealthService, "discover_api_keys", return_value={"jellyfin": "abc"},
        ), patch(
            "urllib.request.urlopen", return_value=_FakeResp(status=200),
        ):
            svc = HealthService()
            result = svc.probe_credentials()
        self.assertEqual(result["credentials"]["jellyfin"], "ok")
        self.assertEqual(result["ok"], 1)

    def test_api_key_service_returns_fail_on_401(self):
        import urllib.error

        def _raise(*_a, **_kw):
            raise urllib.error.HTTPError("http://h/x", 401, "u", {}, None)

        jf = _FakeService(id="jellyfin")
        with self._patch_service_meta([jf]), self._patch_login_probes({
            "jellyfin": ("h", 80, "/login", "json_credentials"),
        }), patch.object(
            HealthService, "discover_api_keys", return_value={"jellyfin": "abc"},
        ), patch("urllib.request.urlopen", side_effect=_raise):
            svc = HealthService()
            result = svc.probe_credentials()
        self.assertEqual(result["credentials"]["jellyfin"], "fail")

    def test_api_key_service_with_no_key_returns_no_key(self):
        jf = _FakeService(id="jellyfin")
        with self._patch_service_meta([jf]), self._patch_login_probes({
            "jellyfin": ("h", 80, "/login", "json_credentials"),
        }), patch.object(
            HealthService, "discover_api_keys", return_value={},  # empty
        ):
            svc = HealthService()
            result = svc.probe_credentials()
        self.assertEqual(result["credentials"]["jellyfin"], "no_key")
        self.assertEqual(result["ok"], 0)

    def test_api_key_service_transport_error_returns_error(self):
        import urllib.error

        def _raise(*_a, **_kw):
            raise urllib.error.URLError("dns fail")

        jf = _FakeService(id="jellyfin")
        with self._patch_service_meta([jf]), self._patch_login_probes({
            "jellyfin": ("h", 80, "/login", "json_credentials"),
        }), patch.object(
            HealthService, "discover_api_keys", return_value={"jellyfin": "abc"},
        ), patch("urllib.request.urlopen", side_effect=_raise):
            svc = HealthService()
            result = svc.probe_credentials()
        self.assertEqual(result["credentials"]["jellyfin"], "error")

    def test_api_key_probe_never_calls_authenticatebyname(self):
        """Regression: the whole point of the split is that Jellyfin's
        ``POST /Users/AuthenticateByName`` is NEVER invoked by the
        health probe path. Assert the urlopen call was GET-only."""
        jf = _FakeService(id="jellyfin")
        captured: list[tuple[str, str]] = []

        def _capture(req, *_a, **_kw):
            captured.append((req.get_method(), req.full_url))
            return _FakeResp(status=200)

        with self._patch_service_meta([jf]), self._patch_login_probes({
            "jellyfin": ("h", 80, "/login", "json_credentials"),
        }), patch.object(
            HealthService, "discover_api_keys", return_value={"jellyfin": "abc"},
        ), patch("urllib.request.urlopen", side_effect=_capture):
            HealthService().probe_credentials()

        # Exactly one GET to the auth_path — no POST, no login endpoint.
        self.assertEqual(len(captured), 1)
        method, url = captured[0]
        self.assertEqual(method, "GET")
        self.assertNotIn("/Users/AuthenticateByName", url)
        self.assertIn("/System/Info", url)


class ProbeCredentialsFallbackTests(unittest.TestCase):
    """Services without a token auth fall back to _probe_login."""

    def test_form_service_still_uses_probe_login(self):
        # qBittorrent: login_mode=form, auth_mode="" → no API-key path.
        qb = _FakeService(
            id="qbittorrent", auth_mode="", login_mode="form",
            login_path="/api/v2/auth/login",
        )
        with patch(
            "media_stack.api.services.health.SERVICES", [qb],
        ), patch(
            "media_stack.api.services.health.LOGIN_PROBES",
            {"qbittorrent": ("h", 80, "/api/v2/auth/login", "form")},
        ), patch.object(
            HealthService, "discover_api_keys", return_value={},
        ), patch(
            "media_stack.api.services.health._probe_login",
            return_value="ok",
        ) as mock_probe:
            HealthService().probe_credentials()
        mock_probe.assert_called_once()


class ProbePasswordPropagationTests(unittest.TestCase):

    def _patch_common(self, keys=None):
        if keys is None:
            keys = {"jellyfin": "abc"}
        jf = _FakeService(id="jellyfin")
        return [
            patch("media_stack.api.services.health.SERVICES", [jf]),
            patch(
                "media_stack.api.services.health.LOGIN_PROBES",
                {"jellyfin": ("h", 80, "/login", "json_credentials")},
            ),
            patch.object(
                HealthService, "discover_api_keys", return_value=keys,
            ),
        ]

    def test_admin_has_password_true_returns_ok(self):
        body = json.dumps([
            {"Name": "admin", "HasPassword": True},
            {"Name": "alice", "HasPassword": True},
        ]).encode()
        patches = self._patch_common()
        patches.append(patch(
            "urllib.request.urlopen",
            return_value=_FakeResp(status=200, body=body),
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            result = HealthService().probe_password_propagation()
        self.assertEqual(result["password_propagation"]["jellyfin"], "ok")

    def test_admin_has_password_false_returns_not_propagated(self):
        body = json.dumps([
            {"Name": "admin", "HasPassword": False},
        ]).encode()
        patches = self._patch_common()
        patches.append(patch(
            "urllib.request.urlopen",
            return_value=_FakeResp(status=200, body=body),
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            result = HealthService().probe_password_propagation()
        self.assertEqual(
            result["password_propagation"]["jellyfin"], "not_propagated",
        )

    def test_admin_not_in_user_list_returns_no_user(self):
        body = json.dumps([
            {"Name": "alice", "HasPassword": True},
        ]).encode()
        patches = self._patch_common()
        patches.append(patch(
            "urllib.request.urlopen",
            return_value=_FakeResp(status=200, body=body),
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            result = HealthService().probe_password_propagation()
        self.assertEqual(
            result["password_propagation"]["jellyfin"], "no_user",
        )

    def test_no_api_key_returns_no_key(self):
        patches = self._patch_common(keys={})
        with patches[0], patches[1], patches[2]:
            result = HealthService().probe_password_propagation()
        self.assertEqual(
            result["password_propagation"]["jellyfin"], "no_key",
        )

    def test_malformed_response_returns_error(self):
        patches = self._patch_common()
        # Non-list body → error.
        patches.append(patch(
            "urllib.request.urlopen",
            return_value=_FakeResp(status=200, body=b'{"not":"a list"}'),
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            result = HealthService().probe_password_propagation()
        self.assertEqual(
            result["password_propagation"]["jellyfin"], "error",
        )

    def test_non_jellyfin_services_report_not_applicable(self):
        arr = _FakeService(
            id="sonarr", auth_mode="X-Api-Key", login_mode="form",
            login_path="/login",
        )
        with patch(
            "media_stack.api.services.health.SERVICES", [arr],
        ), patch(
            "media_stack.api.services.health.LOGIN_PROBES",
            {"sonarr": ("h", 80, "/login", "form")},
        ), patch.object(
            HealthService, "discover_api_keys",
            return_value={"sonarr": "abc"},
        ):
            result = HealthService().probe_password_propagation()
        self.assertEqual(result["password_propagation"]["sonarr"], "n/a")

    def test_propagation_probe_never_attempts_login(self):
        body = json.dumps([
            {"Name": "admin", "HasPassword": True},
        ]).encode()
        captured: list[tuple[str, str]] = []

        def _capture(req, *_a, **_kw):
            captured.append((req.get_method(), req.full_url))
            return _FakeResp(status=200, body=body)

        patches = self._patch_common()
        patches.append(patch(
            "urllib.request.urlopen", side_effect=_capture,
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            HealthService().probe_password_propagation()
        # Exactly one GET to /Users — no POST anywhere.
        self.assertEqual(len(captured), 1)
        method, url = captured[0]
        self.assertEqual(method, "GET")
        self.assertIn("/Users", url)
        self.assertNotIn("/Users/AuthenticateByName", url)


if __name__ == "__main__":
    unittest.main()
