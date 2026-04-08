"""Unit tests for ControllerAPIHandler request routing and helpers.

Tests the handler WITHOUT starting a real HTTP server by constructing
ControllerAPIHandler instances directly with mocked I/O.
"""

import base64
import io
import json
import os
import sys
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.server import ControllerAPIHandler, KNOWN_ACTIONS  # noqa: E402
from media_stack.api.state import ControllerState  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: build a handler without a real socket/server
# ---------------------------------------------------------------------------

def make_handler(method, path, body=None, headers=None, state=None):
    """Create a ControllerAPIHandler instance for testing without a real server."""
    request_line = f"{method} {path} HTTP/1.1\r\n"
    header_lines = headers or {}
    header_str = "".join(f"{k}: {v}\r\n" for k, v in header_lines.items())
    body_bytes = body.encode() if body else b""
    if body_bytes:
        header_str += f"Content-Length: {len(body_bytes)}\r\n"
    handler = ControllerAPIHandler.__new__(ControllerAPIHandler)
    handler.rfile = io.BytesIO(body_bytes)
    handler.wfile = io.BytesIO()
    handler.headers = BaseHTTPRequestHandler.MessageClass()
    for k, v in header_lines.items():
        handler.headers[k] = v
    if body_bytes:
        handler.headers["Content-Length"] = str(len(body_bytes))
    handler.path = path
    handler.command = method
    handler.state = state or ControllerState()
    handler._callbacks = {}
    handler.client_address = ("127.0.0.1", 12345)
    handler.requestline = request_line.strip()
    handler.request_version = "HTTP/1.1"
    handler.close_connection = True
    # Mock send_response, send_header, end_headers to capture output
    handler.send_response = mock.MagicMock()
    handler.send_header = mock.MagicMock()
    handler.end_headers = mock.MagicMock()
    return handler


def _basic_auth(username="admin", password="secret"):
    """Return a Basic Auth header value."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def _get_json_written(handler):
    """Extract the JSON body written to handler.wfile."""
    data = handler.wfile.getvalue()
    if data:
        return json.loads(data.decode())
    return None


def _get_response_code(handler):
    """Return the status code passed to the first send_response call."""
    if handler.send_response.call_args_list:
        return handler.send_response.call_args_list[0][0][0]
    return None


# ===========================================================================
# Auth (_check_auth)
# ===========================================================================

class TestCheckAuthProbes(unittest.TestCase):
    """Probes (/healthz, /readyz) are always public regardless of auth mode."""

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "all"})
    def test_healthz_always_public(self):
        h = make_handler("GET", "/healthz")
        result = h._check_auth()
        self.assertTrue(result)

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "all"})
    def test_readyz_always_public(self):
        h = make_handler("GET", "/readyz")
        result = h._check_auth()
        self.assertTrue(result)


class TestCheckAuthWriteMode(unittest.TestCase):
    """Default 'write' mode: GET is public, POST requires auth."""

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "write"})
    def test_get_allowed_without_auth(self):
        h = make_handler("GET", "/status")
        result = h._check_auth()
        self.assertTrue(result)

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "write"})
    def test_post_without_auth_returns_401(self):
        h = make_handler("POST", "/config")
        result = h._check_auth()
        self.assertFalse(result)
        h.send_response.assert_called_with(401)

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "write"})
    def test_post_with_valid_basic_auth_passes(self):
        h = make_handler("POST", "/config", headers={
            "Authorization": _basic_auth("admin", "secret"),
        })
        result = h._check_auth()
        self.assertTrue(result)

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "write"})
    def test_post_with_invalid_credentials_returns_401(self):
        h = make_handler("POST", "/config", headers={
            "Authorization": _basic_auth("admin", "wrong"),
        })
        result = h._check_auth()
        self.assertFalse(result)
        h.send_response.assert_called_with(401)


class TestCheckAuthNoneMode(unittest.TestCase):
    """When no password is configured, auth defaults to 'none'."""

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "", "CONTROLLER_AUTH": ""}, clear=False)
    def test_no_password_no_auth_required(self):
        # Ensure relevant env vars are empty
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "", "CONTROLLER_AUTH": ""}):
            h = make_handler("POST", "/config")
            result = h._check_auth()
            self.assertTrue(result)


class TestCheckAuthAllMode(unittest.TestCase):
    """auth=all protects GET endpoints too (except probes)."""

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "all"})
    def test_get_without_auth_returns_401(self):
        h = make_handler("GET", "/status")
        result = h._check_auth()
        self.assertFalse(result)
        h.send_response.assert_called_with(401)

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "all"})
    def test_get_with_valid_auth_passes(self):
        h = make_handler("GET", "/status", headers={
            "Authorization": _basic_auth("admin", "secret"),
        })
        result = h._check_auth()
        self.assertTrue(result)


# ===========================================================================
# Response helpers
# ===========================================================================

class TestJsonResponse(unittest.TestCase):
    """_json_response writes correct content-type and JSON body."""

    def test_json_response_status_and_content_type(self):
        h = make_handler("GET", "/test")
        h._json_response(200, {"foo": "bar"})
        h.send_response.assert_called_with(200)
        # Check Content-Type header was sent
        header_calls = {call[0][0]: call[0][1] for call in h.send_header.call_args_list}
        self.assertEqual(header_calls["Content-Type"], "application/json")

    def test_json_response_body_is_valid_json(self):
        h = make_handler("GET", "/test")
        h._json_response(200, {"key": "value", "num": 42})
        body = json.loads(h.wfile.getvalue().decode())
        self.assertEqual(body["key"], "value")
        self.assertEqual(body["num"], 42)

    def test_json_response_content_length_matches_body(self):
        h = make_handler("GET", "/test")
        h._json_response(200, {"a": 1})
        raw = h.wfile.getvalue()
        header_calls = {call[0][0]: call[0][1] for call in h.send_header.call_args_list}
        self.assertEqual(int(header_calls["Content-Length"]), len(raw))


class TestReadJsonBody(unittest.TestCase):
    """_read_json_body parses request bodies."""

    def test_valid_json_parsed(self):
        payload = json.dumps({"key": "value"})
        h = make_handler("POST", "/test", body=payload)
        result = h._read_json_body()
        self.assertEqual(result, {"key": "value"})

    def test_empty_body_returns_empty_dict(self):
        h = make_handler("POST", "/test")
        result = h._read_json_body()
        self.assertEqual(result, {})

    def test_invalid_json_returns_empty_dict(self):
        h = make_handler("POST", "/test", body="not-json{{{")
        result = h._read_json_body()
        self.assertEqual(result, {})


# ===========================================================================
# GET routes
# ===========================================================================

class TestGetHealthz(unittest.TestCase):
    def test_healthz_returns_ok(self):
        h = make_handler("GET", "/healthz")
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_GET()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertEqual(body["status"], "ok")


class TestGetReadyz(unittest.TestCase):
    def test_readyz_returns_readiness_state(self):
        state = ControllerState()
        h = make_handler("GET", "/readyz", state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_GET()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertEqual(body["status"], "ready")
        self.assertFalse(body["initial_bootstrap_done"])
        self.assertEqual(body["phase"], "idle")

    def test_readyz_after_bootstrap_done(self):
        state = ControllerState()
        state.initial_bootstrap_done = True
        state.phase = "complete"
        h = make_handler("GET", "/readyz", state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_GET()
        body = _get_json_written(h)
        self.assertTrue(body["initial_bootstrap_done"])
        self.assertEqual(body["phase"], "complete")


class TestGetStatus(unittest.TestCase):
    def test_status_returns_full_state_dict(self):
        state = ControllerState()
        state.phase = "running"
        h = make_handler("GET", "/status", state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_GET()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertEqual(body["phase"], "running")
        # Verify it matches state.to_dict() keys
        state_dict = state.to_dict()
        for key in ("phase", "initial_bootstrap_done", "runtime_config"):
            self.assertIn(key, body)


class TestGetOpenAPI(unittest.TestCase):
    def test_openapi_json_returns_valid_spec(self):
        h = make_handler("GET", "/api/openapi.json")
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_GET()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertEqual(body["openapi"], "3.0.3")
        self.assertIn("paths", body)
        self.assertIn("info", body)
        self.assertEqual(body["info"]["title"], "Media Stack Controller API")


class TestGetUnknownPath(unittest.TestCase):
    def test_unknown_get_returns_404(self):
        h = make_handler("GET", "/nonexistent/path")
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_GET()
        self.assertEqual(_get_response_code(h), 404)
        body = _get_json_written(h)
        self.assertEqual(body["error"], "not found")


class TestGetConfig(unittest.TestCase):
    def test_config_returns_runtime_config(self):
        state = ControllerState()
        state.runtime_config = {"dry_run": True, "verbose": False}
        h = make_handler("GET", "/config", state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_GET()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertTrue(body["config"]["dry_run"])


class TestGetWebhooks(unittest.TestCase):
    def test_webhooks_returns_url_list(self):
        state = ControllerState()
        # server.py calls .add() so webhook_urls must be set-like at runtime
        state.webhook_urls = {"https://example.com/hook"}
        h = make_handler("GET", "/webhooks", state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_GET()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertIn("https://example.com/hook", body["webhook_urls"])


# ===========================================================================
# POST routes
# ===========================================================================

class TestPostCancel(unittest.TestCase):
    def test_cancel_when_action_running(self):
        state = ControllerState()
        state.start_action("bootstrap")
        h = make_handler("POST", "/cancel", state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertEqual(body["status"], "cancel_requested")
        self.assertIsNotNone(body["current_action"])

    def test_cancel_when_idle(self):
        state = ControllerState()
        h = make_handler("POST", "/cancel", state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertEqual(body["status"], "no_action_running")
        self.assertIsNone(body["current_action"])

    def test_cancel_via_actions_cancel_alias(self):
        state = ControllerState()
        state.start_action("envoy-config")
        h = make_handler("POST", "/actions/cancel", state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(body["status"], "cancel_requested")


class TestPostActions(unittest.TestCase):
    def test_known_action_calls_trigger(self):
        triggered = []
        h = make_handler("POST", "/actions/envoy-config")
        h._callbacks = {
            "action_trigger": lambda name, overrides: triggered.append((name, overrides)),
        }
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertEqual(body["status"], "accepted")
        self.assertEqual(body["action"], "envoy-config")
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0][0], "envoy-config")

    def test_unknown_action_returns_404_with_known_list(self):
        h = make_handler("POST", "/actions/unknown-action")
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 404)
        self.assertIn("unknown action", body["error"])
        self.assertIn("known", body)
        # The known list should match KNOWN_ACTIONS
        self.assertEqual(set(body["known"]), KNOWN_ACTIONS)

    def test_action_with_overrides(self):
        triggered = []
        payload = json.dumps({"dry_run": True})
        h = make_handler("POST", "/actions/bootstrap", body=payload)
        h._callbacks = {
            "action_trigger": lambda name, overrides: triggered.append((name, overrides)),
        }
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(body["action"], "bootstrap")
        self.assertTrue(body["overrides"]["dry_run"])

    def test_action_priority_in_response(self):
        h = make_handler("POST", "/actions/bootstrap")
        h._callbacks = {"action_trigger": lambda n, o: None}
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(body["priority"], 10)  # bootstrap = priority 10


class TestPostConfig(unittest.TestCase):
    def test_config_update_merges_runtime_config(self):
        state = ControllerState()
        state.runtime_config = {"existing": "value"}
        payload = json.dumps({"new_key": "new_value"})
        h = make_handler("POST", "/config", body=payload, state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertEqual(body["status"], "updated")
        self.assertEqual(body["config"]["existing"], "value")
        self.assertEqual(body["config"]["new_key"], "new_value")

    def test_config_empty_body_returns_400(self):
        h = make_handler("POST", "/config")
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        self.assertEqual(_get_response_code(h), 400)
        body = _get_json_written(h)
        self.assertIn("error", body)


class TestPostWebhooks(unittest.TestCase):
    def test_add_webhook_url(self):
        state = ControllerState()
        # server.py calls .add(), so webhook_urls must be set-like
        state.webhook_urls = set()
        payload = json.dumps({"url": "https://example.com/webhook"})
        h = make_handler("POST", "/webhooks", body=payload, state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        self.assertIn("https://example.com/webhook", body["webhook_urls"])

    def test_add_webhook_empty_url_no_error(self):
        state = ControllerState()
        state.webhook_urls = set()
        payload = json.dumps({"url": ""})
        h = make_handler("POST", "/webhooks", body=payload, state=state)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(_get_response_code(h), 200)
        # Empty URLs should not be added
        self.assertEqual(body["webhook_urls"], [])


class TestPostUnknownPath(unittest.TestCase):
    def test_unknown_post_returns_404(self):
        h = make_handler("POST", "/nonexistent")
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        self.assertEqual(_get_response_code(h), 404)


class TestPostRun(unittest.TestCase):
    """POST /run is a backward-compatible alias for bootstrap action."""

    def test_run_triggers_bootstrap(self):
        triggered = []
        h = make_handler("POST", "/run")
        h._callbacks = {
            "action_trigger": lambda name, overrides: triggered.append((name, overrides)),
        }
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        body = _get_json_written(h)
        self.assertEqual(body["action"], "bootstrap")
        self.assertEqual(len(triggered), 1)


class TestPostResetPassword(unittest.TestCase):
    """POST /api/reset-password validation."""

    @mock.patch("media_stack.api.services.admin.reset_password", return_value={"status": "ok"})
    def test_reset_password_with_valid_password(self, mock_reset):
        payload = json.dumps({"password": "newpass1234"})
        h = make_handler("POST", "/api/reset-password", body=payload)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        self.assertEqual(_get_response_code(h), 200)
        mock_reset.assert_called_once_with("newpass1234", None)

    def test_reset_password_too_short_returns_400(self):
        payload = json.dumps({"password": "ab"})
        h = make_handler("POST", "/api/reset-password", body=payload)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        self.assertEqual(_get_response_code(h), 400)
        body = _get_json_written(h)
        self.assertIn("min 4 chars", body["error"])

    def test_reset_password_missing_returns_400(self):
        payload = json.dumps({})
        h = make_handler("POST", "/api/reset-password", body=payload)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        self.assertEqual(_get_response_code(h), 400)


class TestPostBatchRestart(unittest.TestCase):
    """POST /api/batch-restart validation."""

    def test_batch_restart_empty_services_returns_400(self):
        payload = json.dumps({"services": []})
        h = make_handler("POST", "/api/batch-restart", body=payload)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        self.assertEqual(_get_response_code(h), 400)
        body = _get_json_written(h)
        self.assertIn("services list required", body["error"])

    @mock.patch("media_stack.api.services.admin.batch_restart", return_value={"restarted": ["sonarr"]})
    def test_batch_restart_with_services(self, mock_restart):
        payload = json.dumps({"services": ["sonarr"]})
        h = make_handler("POST", "/api/batch-restart", body=payload)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        self.assertEqual(_get_response_code(h), 200)
        mock_restart.assert_called_once_with(["sonarr"])


class TestPostGuardrails(unittest.TestCase):
    """POST /api/guardrails validation."""

    def test_guardrails_empty_body_returns_400(self):
        h = make_handler("POST", "/api/guardrails")
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        self.assertEqual(_get_response_code(h), 400)
        body = _get_json_written(h)
        self.assertIn("JSON body required", body["error"])

    @mock.patch("media_stack.api.services.disk.update_guardrails", return_value={"status": "updated"})
    def test_guardrails_with_body(self, mock_update):
        payload = json.dumps({"max_size_gb": 500})
        h = make_handler("POST", "/api/guardrails", body=payload)
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_POST()
        self.assertEqual(_get_response_code(h), 200)
        mock_update.assert_called_once_with({"max_size_gb": 500})


# ===========================================================================
# Auth + POST integration
# ===========================================================================

class TestAuthProtectedPOSTEndpoints(unittest.TestCase):
    """POST to auth-required paths must carry valid credentials in write mode."""

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "write"})
    def test_post_config_without_auth_returns_401(self):
        payload = json.dumps({"key": "val"})
        h = make_handler("POST", "/config", body=payload)
        h.do_POST()
        h.send_response.assert_called_with(401)

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "write"})
    def test_post_config_with_auth_succeeds(self):
        payload = json.dumps({"key": "val"})
        h = make_handler("POST", "/config", body=payload, headers={
            "Authorization": _basic_auth("admin", "secret"),
        })
        h.do_POST()
        self.assertEqual(_get_response_code(h), 200)

    @mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "write"})
    def test_post_cancel_without_auth_returns_401(self):
        h = make_handler("POST", "/cancel")
        h.do_POST()
        h.send_response.assert_called_with(401)


# ===========================================================================
# Custom username
# ===========================================================================

class TestCustomUsername(unittest.TestCase):
    """STACK_ADMIN_USERNAME overrides the default 'admin'."""

    @mock.patch.dict(os.environ, {
        "STACK_ADMIN_USERNAME": "ops",
        "STACK_ADMIN_PASSWORD": "pass123",
        "CONTROLLER_AUTH": "write",
    })
    def test_custom_username_auth(self):
        h = make_handler("POST", "/config", body='{"x":1}', headers={
            "Authorization": _basic_auth("ops", "pass123"),
        })
        result = h._check_auth()
        self.assertTrue(result)

    @mock.patch.dict(os.environ, {
        "STACK_ADMIN_USERNAME": "ops",
        "STACK_ADMIN_PASSWORD": "pass123",
        "CONTROLLER_AUTH": "write",
    })
    def test_default_username_rejected_when_custom_set(self):
        h = make_handler("POST", "/config", body='{"x":1}', headers={
            "Authorization": _basic_auth("admin", "pass123"),
        })
        result = h._check_auth()
        self.assertFalse(result)


# ===========================================================================
# HTML response helper
# ===========================================================================

class TestHtmlResponse(unittest.TestCase):
    def test_html_response_content_type(self):
        h = make_handler("GET", "/test")
        h._html_response(200, "<html><body>hi</body></html>")
        header_calls = {call[0][0]: call[0][1] for call in h.send_header.call_args_list}
        self.assertEqual(header_calls["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(_get_response_code(h), 200)


# ===========================================================================
# Query string handling
# ===========================================================================

class TestQueryStringHandling(unittest.TestCase):
    """Path matching strips query strings for route dispatch."""

    def test_healthz_with_query_string(self):
        h = make_handler("GET", "/healthz?foo=bar")
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": ""}):
            h.do_GET()
        body = _get_json_written(h)
        self.assertEqual(body["status"], "ok")

    def test_auth_check_strips_query_string(self):
        """Auth check should match /healthz even with query params."""
        with mock.patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "secret", "CONTROLLER_AUTH": "all"}):
            h = make_handler("GET", "/healthz?ts=123")
            result = h._check_auth()
            self.assertTrue(result)


class TestApiDocsEndpoints(unittest.TestCase):
    """GET /api/docs and /api/openapi.yaml serve documentation."""

    def test_api_docs_returns_html(self):
        h = make_handler("GET", "/api/docs")
        h.do_GET()
        self.assertEqual(_get_response_code(h), 200)
        body = h.wfile.getvalue()
        self.assertIn(b"redoc", body.lower())

    def test_openapi_yaml_returns_200(self):
        h = make_handler("GET", "/api/openapi.yaml")
        h.do_GET()
        self.assertEqual(_get_response_code(h), 200)
        body = h.wfile.getvalue()
        self.assertIn(b"openapi", body.lower())


if __name__ == "__main__":
    unittest.main()
