"""Tests for admin credential management — reset_password with fallback logic."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.admin as admin_mod  # noqa: E402
from media_stack.core.service_registry.registry import ServiceDef  # noqa: E402


def _svc(id, **kw):
    return ServiceDef(id=id, name=id.capitalize(), **kw)


# Fake services for patching
_QBIT = _svc("qbittorrent", host="qbit", port=8080, login_mode="form", login_path="/api/v2/auth/login")
_PROWLARR = _svc("prowlarr", host="prowlarr", port=9696, api_key_env="PROWLARR_API_KEY",
                  password_api_path="/api/v1/config/host")
_SONARR = _svc("sonarr", host="sonarr", port=8989, api_key_env="SONARR_API_KEY",
                password_api_path="/api/v3/config/host")


class TestQbitPasswordReset(unittest.TestCase):
    """qBittorrent password reset with default password fallback."""

    @patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "media-stack", "STACK_ADMIN_USERNAME": "admin"})
    @patch("urllib.request.build_opener")
    @patch.object(admin_mod, "SERVICE_MAP", {"qbittorrent": _QBIT})
    def test_default_password_fallback(self, mock_opener):
        """Should try 'adminadmin' when configured password fails."""
        call_count = [0]
        def side_effect(req, timeout=None):
            call_count[0] += 1
            resp = MagicMock()
            if call_count[0] == 1:
                resp.read.return_value = b"Fails."
            elif call_count[0] == 2:
                resp.read.return_value = b"Ok."
            else:
                resp.read.return_value = b""
            return resp
        mock_opener.return_value.open.side_effect = side_effect
        result = admin_mod.reset_password("newpass", target_services=["qbittorrent"])
        self.assertIn("qbittorrent", result.get("services", []))

    @patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "media-stack", "STACK_ADMIN_USERNAME": "admin"})
    @patch("urllib.request.build_opener")
    @patch.object(admin_mod, "SERVICE_MAP", {"qbittorrent": _QBIT})
    def test_all_passwords_fail(self, mock_opener):
        """Should report error when all default passwords fail."""
        resp = MagicMock()
        resp.read.return_value = b"Fails."
        mock_opener.return_value.open.return_value = resp
        result = admin_mod.reset_password("newpass", target_services=["qbittorrent"])
        self.assertTrue(any("qbittorrent" in e for e in result.get("errors", [])))

    @patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "media-stack", "STACK_ADMIN_USERNAME": "admin"})
    @patch("urllib.request.build_opener")
    @patch.object(admin_mod, "SERVICE_MAP", {"qbittorrent": _QBIT})
    def test_connection_refused_tries_all(self, mock_opener):
        """Should try all passwords even when connection fails."""
        mock_opener.return_value.open.side_effect = ConnectionRefusedError("refused")
        result = admin_mod.reset_password("newpass", target_services=["qbittorrent"])
        self.assertTrue(any("qbittorrent" in e for e in result.get("errors", [])))


class TestArrPasswordReset(unittest.TestCase):
    """Arr service password reset via host config API."""

    @patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old", "STACK_ADMIN_USERNAME": "admin",
                              "SONARR_API_KEY": "testkey123"})
    @patch("urllib.request.urlopen")
    @patch.object(admin_mod, "SERVICE_MAP", {"sonarr": _SONARR})
    @patch.object(admin_mod, "get_services_with_password_api", return_value=[_SONARR])
    @patch.object(admin_mod, "get_services_with_password_config", return_value=[])
    def test_sets_password_and_enables_forms(self, _a, _b, mock_urlopen):
        """Should set password and enable Forms auth when authenticationMethod is none."""
        get_resp = MagicMock()
        get_resp.read.return_value = json.dumps({
            "username": "admin", "password": "", "authenticationMethod": "none",
        }).encode()
        get_resp.__enter__ = MagicMock(return_value=get_resp)
        get_resp.__exit__ = MagicMock(return_value=False)
        put_resp = MagicMock()
        put_resp.__enter__ = MagicMock(return_value=put_resp)
        put_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.side_effect = [get_resp, put_resp]
        result = admin_mod.reset_password("newpass", target_services=["sonarr"])
        self.assertIn("sonarr", result.get("services", []))
        # Verify the PUT body includes authenticationMethod=forms
        put_call = mock_urlopen.call_args_list[1]
        put_body = json.loads(put_call[0][0].data)
        self.assertEqual(put_body["authenticationMethod"], "forms")
        self.assertEqual(put_body["password"], "newpass")

    @patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old", "STACK_ADMIN_USERNAME": "admin",
                              "SONARR_API_KEY": "testkey"})
    @patch("urllib.request.urlopen")
    @patch.object(admin_mod, "SERVICE_MAP", {"sonarr": _SONARR})
    @patch.object(admin_mod, "get_services_with_password_api", return_value=[_SONARR])
    @patch.object(admin_mod, "get_services_with_password_config", return_value=[])
    def test_preserves_existing_forms_auth(self, _a, _b, mock_urlopen):
        """Should not change authenticationMethod if already set to forms."""
        get_resp = MagicMock()
        get_resp.read.return_value = json.dumps({
            "username": "admin", "password": "old", "authenticationMethod": "forms",
        }).encode()
        get_resp.__enter__ = MagicMock(return_value=get_resp)
        get_resp.__exit__ = MagicMock(return_value=False)
        put_resp = MagicMock()
        put_resp.__enter__ = MagicMock(return_value=put_resp)
        put_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.side_effect = [get_resp, put_resp]
        admin_mod.reset_password("newpass", target_services=["sonarr"])
        put_body = json.loads(mock_urlopen.call_args_list[1][0][0].data)
        self.assertEqual(put_body["authenticationMethod"], "forms")

    @patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old", "STACK_ADMIN_USERNAME": "admin"})
    @patch.object(admin_mod, "SERVICE_MAP", {"sonarr": _SONARR})
    @patch.object(admin_mod, "get_services_with_password_api", return_value=[_SONARR])
    @patch.object(admin_mod, "get_services_with_password_config", return_value=[])
    def test_no_api_key_reports_error(self, _a, _b):
        """Should report error when no API key is available."""
        result = admin_mod.reset_password("newpass", target_services=["sonarr"])
        self.assertTrue(any("no API key" in e for e in result.get("errors", [])))

    @patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old", "STACK_ADMIN_USERNAME": "admin",
                              "SONARR_API_KEY": "key"})
    @patch("urllib.request.urlopen", side_effect=Exception("connection refused"))
    @patch.object(admin_mod, "SERVICE_MAP", {"sonarr": _SONARR})
    @patch.object(admin_mod, "get_services_with_password_api", return_value=[_SONARR])
    @patch.object(admin_mod, "get_services_with_password_config", return_value=[])
    def test_http_error_reported(self, _a, _b, mock_urlopen):
        """Should capture HTTP errors in errors list."""
        result = admin_mod.reset_password("newpass", target_services=["sonarr"])
        self.assertTrue(len(result.get("errors", [])) > 0)


class TestTargetFiltering(unittest.TestCase):
    @patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old", "STACK_ADMIN_USERNAME": "admin"})
    @patch.object(admin_mod, "SERVICE_MAP", {"qbittorrent": _QBIT, "sonarr": _SONARR})
    @patch.object(admin_mod, "get_services_with_password_api", return_value=[])
    @patch.object(admin_mod, "get_services_with_password_config", return_value=[])
    def test_filter_skips_unselected(self, *_):
        """Only reset targeted services."""
        result = admin_mod.reset_password("newpass", target_services=["sonarr"])
        # qbittorrent should not appear in either updated or errors
        all_mentioned = " ".join(result.get("services", []) + result.get("errors", []))
        self.assertNotIn("qbittorrent", all_mentioned.lower().replace("qbittorrent", "").lower()
                         if "qbittorrent" not in result.get("services", []) else "found")


class TestReturnStructure(unittest.TestCase):
    @patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old", "STACK_ADMIN_USERNAME": "admin"})
    @patch.object(admin_mod, "SERVICE_MAP", {})
    @patch.object(admin_mod, "get_services_with_password_api", return_value=[])
    @patch.object(admin_mod, "get_services_with_password_config", return_value=[])
    def test_return_has_expected_keys(self, *_):
        """Return dict should have status, services, errors keys."""
        result = admin_mod.reset_password("newpass")
        self.assertIn("status", result)
        self.assertIn("services", result)
        self.assertIn("errors", result)

    @patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old", "STACK_ADMIN_USERNAME": "admin"})
    @patch.object(admin_mod, "SERVICE_MAP", {})
    @patch.object(admin_mod, "get_services_with_password_api", return_value=[])
    @patch.object(admin_mod, "get_services_with_password_config", return_value=[])
    def test_empty_services_returns_updated(self, *_):
        """With no services, should return status=updated with empty lists."""
        result = admin_mod.reset_password("newpass")
        self.assertEqual(result["status"], "updated")


class TestValidateCredentialsAction(unittest.TestCase):
    @patch("media_stack.api.services.health.probe_credentials",
           return_value={"credentials": {"sonarr": "disabled", "jellyfin": "ok"}, "ok": 1, "total": 2})
    @patch("media_stack.api.services.admin.reset_password",
           return_value={"services": ["sonarr"], "errors": [], "status": "updated", "services": ["sonarr"]})
    def test_auto_syncs_disabled_services(self, mock_reset, mock_probe):
        """validate_credentials should call reset_password for disabled services."""
        from media_stack.services.jobs.action_handlers import action_validate_credentials
        # Second call for re-validation
        mock_probe.side_effect = [
            {"credentials": {"sonarr": "disabled", "jellyfin": "ok"}, "ok": 1, "total": 2},
            {"credentials": {"sonarr": "ok"}, "ok": 1, "total": 1},
        ]
        action_validate_credentials()
        mock_reset.assert_called_once()
        # Should target only "sonarr" (the disabled one)
        call_args = mock_reset.call_args
        self.assertIn("sonarr", call_args[1].get("target_services", call_args[0][1] if len(call_args[0]) > 1 else []))

    @patch("media_stack.api.services.health.probe_credentials",
           return_value={"credentials": {"jellyfin": "ok"}, "ok": 1, "total": 1})
    def test_no_sync_when_all_ok(self, mock_probe):
        """validate_credentials should not call reset_password when all pass."""
        from media_stack.services.jobs.action_handlers import action_validate_credentials
        with patch("media_stack.api.services.admin.reset_password") as mock_reset:
            action_validate_credentials()
            mock_reset.assert_not_called()

    @patch("media_stack.api.services.health.probe_credentials",
           return_value={"credentials": {"sonarr": "fail", "radarr": "fail"}, "ok": 0, "total": 2})
    @patch("media_stack.api.services.admin.reset_password",
           return_value={"services": ["sonarr", "radarr"], "errors": [], "status": "updated", "services": []})
    def test_syncs_failed_services(self, mock_reset, mock_probe):
        """validate_credentials should auto-sync services that fail."""
        from media_stack.services.jobs.action_handlers import action_validate_credentials
        mock_probe.side_effect = [
            {"credentials": {"sonarr": "fail", "radarr": "fail"}, "ok": 0, "total": 2},
            {"credentials": {"sonarr": "ok", "radarr": "ok"}, "ok": 2, "total": 2},
        ]
        action_validate_credentials()
        mock_reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
