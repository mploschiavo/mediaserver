"""Unit tests for media_stack.api.server HTTP endpoints."""

import json
import unittest
from http.client import HTTPConnection

from media_stack.api.server import start_api_server
from media_stack.api.state import ControllerState as BootstrapState


class TestBootstrapAPIServerHealthz(unittest.TestCase):
    def test_healthz(self):
        state = BootstrapState()
        server = start_api_server(state, port=19101)
        try:
            conn = HTTPConnection("127.0.0.1", 19101, timeout=5)
            conn.request("GET", "/healthz")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            self.assertEqual(resp.status, 200)
            self.assertEqual(body["status"], "ok")
            conn.close()
        finally:
            server.shutdown()


class TestBootstrapAPIServerStatus(unittest.TestCase):
    def test_status_idle(self):
        state = BootstrapState()
        server = start_api_server(state, port=19102)
        try:
            conn = HTTPConnection("127.0.0.1", 19102, timeout=5)
            conn.request("GET", "/status")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            self.assertEqual(body["phase"], "idle")
            conn.close()
        finally:
            server.shutdown()

    def test_readyz_200_when_idle(self):
        """Service is ready as soon as it's listening (Deployment model)."""
        state = BootstrapState()
        server = start_api_server(state, port=19103)
        try:
            conn = HTTPConnection("127.0.0.1", 19103, timeout=5)
            conn.request("GET", "/readyz")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.read())
            self.assertFalse(body["initial_bootstrap_done"])
            conn.close()
        finally:
            server.shutdown()

    def test_readyz_200_when_complete(self):
        state = BootstrapState()
        state.start()
        state.finish()
        server = start_api_server(state, port=19104)
        try:
            conn = HTTPConnection("127.0.0.1", 19104, timeout=5)
            conn.request("GET", "/readyz")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            conn.close()
        finally:
            server.shutdown()


class TestBootstrapAPIServerRouting(unittest.TestCase):
    def test_404_on_unknown_get(self):
        state = BootstrapState()
        server = start_api_server(state, port=19105)
        try:
            conn = HTTPConnection("127.0.0.1", 19105, timeout=5)
            conn.request("GET", "/nonexistent")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 404)
            conn.close()
        finally:
            server.shutdown()

    def test_404_on_unknown_post(self):
        state = BootstrapState()
        server = start_api_server(state, port=19106)
        try:
            conn = HTTPConnection("127.0.0.1", 19106, timeout=5)
            conn.request("POST", "/nonexistent")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 404)
            conn.close()
        finally:
            server.shutdown()

    def test_run_200_without_trigger(self):
        """POST /run returns 200 accepted even without action trigger."""
        state = BootstrapState()
        server = start_api_server(state, port=19107)
        try:
            conn = HTTPConnection("127.0.0.1", 19107, timeout=5)
            conn.request("POST", "/run")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            conn.close()
        finally:
            server.shutdown()

    def test_run_200_with_trigger(self):
        """POST /run accepts bootstrap action when trigger is bound."""
        state = BootstrapState()
        triggered = []
        server = start_api_server(
            state, port=19108,
            action_trigger=lambda name, overrides: triggered.append((name, overrides)),
        )
        try:
            conn = HTTPConnection("127.0.0.1", 19108, timeout=5)
            conn.request("POST", "/run")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertEqual(triggered[0][0], "bootstrap")
            conn.close()
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
