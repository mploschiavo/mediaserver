"""Unit tests for dispatch-related functions in controller_main.py.

Covers:
- _dispatch_action: routing table for all 7 known actions + unknown action
- _apply_profile_env: env vars set from YAML profile
- _validate_key_against_service: HTTP key validation with mocks
- _fire_webhooks: webhook POST delivery
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.commands.controller_main import (
    _apply_overrides,
    _apply_profile_env,
    _dispatch_action,
    _validate_key_against_service,
)
from media_stack.api.server import _fire_webhooks
from media_stack.api.state import ControllerState


def _make_args(**overrides):
    """Build a minimal argparse.Namespace matching what _dispatch_action expects."""
    defaults = dict(
        config="/tmp/fake-config.json",
        config_root="/tmp/fake-config",
        wait_timeout=10,
        auto_prowlarr_indexers=False,
        mode="full",
        env="prod",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _dispatch_action routing table
# ---------------------------------------------------------------------------

_HANDLER_MODULE = "media_stack.services.jobs.action_handlers"


class TestDispatchActionBootstrap(unittest.TestCase):
    """_dispatch_action routes 'bootstrap' to action_bootstrap."""

    @mock.patch(f"{_HANDLER_MODULE}.action_bootstrap")
    def test_bootstrap(self, m_handler):
        _dispatch_action("bootstrap", {}, _make_args(), ControllerState())
        m_handler.assert_called_once()


class TestDispatchActionFinalize(unittest.TestCase):
    @mock.patch(f"{_HANDLER_MODULE}.action_post_setup")
    def test_finalize(self, m_handler):
        _dispatch_action("post-setup", {}, _make_args(), ControllerState())
        m_handler.assert_called_once()


class TestDispatchActionAutoIndexers(unittest.TestCase):
    @mock.patch(f"{_HANDLER_MODULE}.action_discover_indexers")
    def test_auto_indexers(self, m_handler):
        _dispatch_action("discover-indexers", {}, _make_args(), ControllerState())
        m_handler.assert_called_once()


class TestDispatchActionRestartApps(unittest.TestCase):
    @mock.patch(f"{_HANDLER_MODULE}.action_restart_apps")
    def test_restart_apps(self, m_handler):
        _dispatch_action("restart-apps", {}, _make_args(), ControllerState())
        m_handler.assert_called_once()


class TestDispatchActionSyncIndexers(unittest.TestCase):
    @mock.patch(f"{_HANDLER_MODULE}.action_push_indexers")
    def test_sync_indexers(self, m_handler):
        _dispatch_action("push-indexers", {}, _make_args(), ControllerState())
        m_handler.assert_called_once()


class TestDispatchActionEnvoyConfig(unittest.TestCase):
    @mock.patch(f"{_HANDLER_MODULE}.action_envoy_config")
    def test_envoy_config(self, m_handler):
        _dispatch_action("envoy-config", {}, _make_args(), ControllerState())
        m_handler.assert_called_once()


class TestDispatchActionReconcile(unittest.TestCase):
    @mock.patch(f"{_HANDLER_MODULE}.action_reconcile")
    def test_reconcile(self, m_handler):
        _dispatch_action("reconcile", {}, _make_args(), ControllerState())
        m_handler.assert_called_once()


class TestDispatchActionUnknown(unittest.TestCase):
    def test_unknown_action_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _dispatch_action("no-such-action", {}, _make_args(), ControllerState())
        self.assertIn("no-such-action", str(ctx.exception))


# ---------------------------------------------------------------------------
# _apply_overrides
# ---------------------------------------------------------------------------

class TestApplyOverrides(unittest.TestCase):
    def test_overrides_set_env_vars(self):
        """Boolean overrides are translated to '1'/'0' env vars."""
        saved = {
            k: os.environ.pop(k, None)
            for k in ("AUTO_DOWNLOAD_CONTENT", "PRECONFIGURE_API_KEYS", "APPLY_INITIAL_PREFERENCES")
        }
        try:
            _apply_overrides({
                "auto_download_content": True,
                "preconfigure_api_keys": False,
            })
            self.assertEqual(os.environ["AUTO_DOWNLOAD_CONTENT"], "1")
            self.assertEqual(os.environ["PRECONFIGURE_API_KEYS"], "0")
            # Keys not in overrides should not be set
            self.assertNotIn("APPLY_INITIAL_PREFERENCES", os.environ)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


# ---------------------------------------------------------------------------
# _apply_profile_env
# ---------------------------------------------------------------------------

class TestApplyProfileEnv(unittest.TestCase):
    def _write_profile(self, content: str) -> str:
        """Write profile YAML to a temp file and return the path."""
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        )
        f.write(content)
        f.close()
        return f.name

    def test_sets_env_from_profile(self):
        """Env vars are populated from bootstrap and routing sections."""
        profile_path = self._write_profile(
            "bootstrap:\n"
            "  apply_initial_preferences: true\n"
            "  preconfigure_api_keys: true\n"
            "  auto_download_content: false\n"
            "routing:\n"
            "  strategy: path-prefix\n"
            "  gateway_host: apps.local\n"
            "  gateway_port: 8443\n"
            "  app_path_prefix: /stack\n"
            "metadata:\n"
            "  purpose: dev\n"
        )
        # Clear the env vars that _apply_profile_env will set
        keys = [
            "FULLY_PRECONFIGURED", "PRECONFIGURE_API_KEYS",
            "APPLY_INITIAL_PREFERENCES", "AUTO_DOWNLOAD_CONTENT",
            "MEDIA_STACK_ENV", "APP_GATEWAY_HOST", "APP_GATEWAY_PORT",
            "APP_PATH_PREFIX", "ROUTE_STRATEGY",
        ]
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            _apply_profile_env(profile_path)

            self.assertEqual(os.environ.get("FULLY_PRECONFIGURED"), "1")
            self.assertEqual(os.environ.get("PRECONFIGURE_API_KEYS"), "1")
            self.assertEqual(os.environ.get("APPLY_INITIAL_PREFERENCES"), "1")
            self.assertEqual(os.environ.get("AUTO_DOWNLOAD_CONTENT"), "0")
            self.assertEqual(os.environ.get("MEDIA_STACK_ENV"), "dev")
            self.assertEqual(os.environ.get("APP_GATEWAY_HOST"), "apps.local")
            self.assertEqual(os.environ.get("APP_GATEWAY_PORT"), "8443")
            self.assertEqual(os.environ.get("APP_PATH_PREFIX"), "/stack")
            self.assertEqual(os.environ.get("ROUTE_STRATEGY"), "path-prefix")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            os.unlink(profile_path)

    def test_does_not_overwrite_existing_env(self):
        """Existing env vars take precedence over profile values."""
        profile_path = self._write_profile(
            "routing:\n  strategy: subdomain\n"
        )
        # _apply_profile_env sets all keys in its env_map, not just routing.
        # Save/restore everything it might touch.
        keys = [
            "FULLY_PRECONFIGURED", "PRECONFIGURE_API_KEYS",
            "APPLY_INITIAL_PREFERENCES", "AUTO_DOWNLOAD_CONTENT",
            "MEDIA_STACK_ENV", "APP_GATEWAY_HOST", "APP_GATEWAY_PORT",
            "APP_PATH_PREFIX", "ROUTE_STRATEGY",
        ]
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            os.environ["ROUTE_STRATEGY"] = "already-set"
            _apply_profile_env(profile_path)
            self.assertEqual(os.environ["ROUTE_STRATEGY"], "already-set")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            os.unlink(profile_path)

    def test_none_profile_is_noop(self):
        """Passing None as the profile path does nothing."""
        _apply_profile_env(None)  # should not raise

    def test_missing_file_is_noop(self):
        """A non-existent file path does nothing."""
        _apply_profile_env("/tmp/this-file-does-not-exist-12345.yaml")

    def test_empty_profile_sets_defaults(self):
        """An empty YAML file sets the default/falsy values."""
        profile_path = self._write_profile("{}\n")
        keys = [
            "FULLY_PRECONFIGURED", "PRECONFIGURE_API_KEYS",
            "APPLY_INITIAL_PREFERENCES", "AUTO_DOWNLOAD_CONTENT",
            "MEDIA_STACK_ENV", "APP_GATEWAY_HOST", "APP_GATEWAY_PORT",
            "APP_PATH_PREFIX", "ROUTE_STRATEGY",
        ]
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            _apply_profile_env(profile_path)
            self.assertEqual(os.environ.get("FULLY_PRECONFIGURED"), "0")
            self.assertEqual(os.environ.get("APP_PATH_PREFIX"), "/app")
            self.assertEqual(os.environ.get("ROUTE_STRATEGY"), "hybrid")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            os.unlink(profile_path)


# ---------------------------------------------------------------------------
# _validate_key_against_service
# ---------------------------------------------------------------------------

class TestValidateKeyAgainstService(unittest.TestCase):
    def _make_service_def(self, **kwargs):
        """Build a ServiceDef-like object with required fields."""
        from media_stack.api.services.registry import ServiceDef
        defaults = dict(
            id="sonarr", name="Sonarr", host="localhost", port=8989,
            health_path="/ping", auth_path="/api/v3/system/status",
            auth_mode="X-Api-Key", api_key_env="SONARR_API_KEY",
            api_key_config="sonarr/config.xml", api_key_format="xml",
        )
        defaults.update(kwargs)
        return ServiceDef(**defaults)

    @mock.patch("media_stack.api.services.registry.SERVICES", new_callable=list)
    def test_matching_key_returns_silently(self, mock_services):
        """A 200 response means the key is valid -- no warning logged."""
        svc = self._make_service_def()
        mock_services.append(svc)
        log_messages = []

        with mock.patch("urllib.request.urlopen") as m_urlopen:
            mock_resp = mock.MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = mock.MagicMock(return_value=False)
            m_urlopen.return_value = mock_resp

            _validate_key_against_service(
                {"SONARR_API_KEY": "abc123"},
                "/srv-config",
                lambda msg: log_messages.append(msg),
            )

        # No warning should have been logged
        self.assertFalse(any("WARN" in m for m in log_messages))

    @mock.patch("media_stack.api.services.registry.SERVICES", new_callable=list)
    def test_401_logs_mismatch_warning(self, mock_services):
        """A 401 means the key does not match the running service."""
        import urllib.error
        svc = self._make_service_def()
        mock_services.append(svc)
        log_messages = []

        with mock.patch("urllib.request.urlopen") as m_urlopen:
            m_urlopen.side_effect = urllib.error.HTTPError(
                url="http://localhost:8989/api/v3/system/status",
                code=401, msg="Unauthorized", hdrs=None, fp=None,
            )

            _validate_key_against_service(
                {"SONARR_API_KEY": "wrong-key"},
                "/srv-config",
                lambda msg: log_messages.append(msg),
            )

        self.assertTrue(any("mismatch" in m.lower() for m in log_messages))

    @mock.patch("media_stack.api.services.registry.SERVICES", new_callable=list)
    def test_no_canary_returns_early(self, mock_services):
        """When no service has a discovered key, validation is skipped."""
        svc = self._make_service_def()
        mock_services.append(svc)
        log_messages = []

        # Pass an empty discovered dict -- no canary found
        _validate_key_against_service(
            {}, "/srv-config", lambda msg: log_messages.append(msg),
        )
        self.assertEqual(log_messages, [])

    @mock.patch("media_stack.api.services.registry.SERVICES", new_callable=list)
    def test_connection_error_is_ignored(self, mock_services):
        """Network errors (service not ready) are silently ignored."""
        svc = self._make_service_def()
        mock_services.append(svc)
        log_messages = []

        with mock.patch("urllib.request.urlopen") as m_urlopen:
            m_urlopen.side_effect = ConnectionRefusedError("not ready")

            _validate_key_against_service(
                {"SONARR_API_KEY": "abc123"},
                "/srv-config",
                lambda msg: log_messages.append(msg),
            )

        self.assertFalse(any("WARN" in m for m in log_messages))


# ---------------------------------------------------------------------------
# _fire_webhooks
# ---------------------------------------------------------------------------

class TestFireWebhooks(unittest.TestCase):
    """Test that _fire_webhooks POSTs JSON payloads to registered URLs."""

    def test_posts_to_webhook_url(self):
        """Webhook URL receives a POST with JSON event payload."""
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received.append(json.loads(body))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass  # suppress stderr

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()

        state = ControllerState()
        state.webhook_urls.append(f"http://127.0.0.1:{port}/hook")

        _fire_webhooks(state, "action_complete", {"action": "bootstrap", "status": "ok"})
        t.join(timeout=5)
        server.server_close()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["event"], "action_complete")
        self.assertEqual(received[0]["action"], "bootstrap")

    def test_no_urls_is_noop(self):
        """No webhook URLs registered means nothing happens."""
        state = ControllerState()
        # Should not raise
        _fire_webhooks(state, "test_event", {"key": "val"})

    def test_unreachable_url_does_not_raise(self):
        """Webhooks are best-effort -- unreachable URLs are silently ignored."""
        state = ControllerState()
        state.webhook_urls.append("http://127.0.0.1:1/nonexistent")
        # Should not raise
        _fire_webhooks(state, "error_event", {"err": "boom"})


if __name__ == "__main__":
    unittest.main()
