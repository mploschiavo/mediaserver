"""Unit tests for _WebhookHmacVerifier."""

from __future__ import annotations

import hashlib
import hmac as _hmac
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.handlers_post import _WebhookHmacVerifier


class _FakeHeaders:
    def __init__(self, mapping):
        self._m = mapping

    def get(self, name, default=""):
        return self._m.get(name, default)


class _FakeHandler:
    def __init__(self, body: bytes, extra_headers=None):
        self.headers = _FakeHeaders({
            "Content-Length": str(len(body)),
            **(extra_headers or {}),
        })
        self.rfile = io.BytesIO(body)
        self._body_bytes = body

    def _read_json_body(self):
        """Mirrors ControllerAPIHandler._read_json_body for the no-secret
        fall-through path in _WebhookHmacVerifier.verify_and_parse."""
        import json as _json
        try:
            return _json.loads(self._body_bytes)
        except (ValueError, TypeError):
            return {}


SECRET = "super-secret-hmac-key"


def _sig_for(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + _hmac.new(
        secret.encode(), body, hashlib.sha256,
    ).hexdigest()


class WebhookHmacTests(unittest.TestCase):
    def setUp(self):
        self.v = _WebhookHmacVerifier()

    def test_no_secret_configured_passes_through(self):
        """Backward compat: no secret = accept every body."""
        body = b'{"eventType":"Download"}'
        h = _FakeHandler(body)
        with mock.patch.dict("os.environ",
                             {"WEBHOOK_HMAC_SECRET": ""}, clear=False):
            parsed, ok = self.v.verify_and_parse(h)
        self.assertTrue(ok)
        self.assertEqual(parsed["eventType"], "Download")

    def test_valid_signature_passes(self):
        body = b'{"eventType":"Download"}'
        h = _FakeHandler(
            body, {"X-Hub-Signature-256": _sig_for(body)},
        )
        with mock.patch.dict("os.environ",
                             {"WEBHOOK_HMAC_SECRET": SECRET}, clear=False):
            parsed, ok = self.v.verify_and_parse(h)
        self.assertTrue(ok)
        self.assertEqual(parsed["eventType"], "Download")

    def test_wrong_signature_rejected(self):
        body = b'{"eventType":"Download"}'
        h = _FakeHandler(
            body, {"X-Hub-Signature-256": "sha256=" + "0" * 64},
        )
        with mock.patch.dict("os.environ",
                             {"WEBHOOK_HMAC_SECRET": SECRET}, clear=False):
            _, ok = self.v.verify_and_parse(h)
        self.assertFalse(ok)

    def test_missing_header_with_secret_set_rejected(self):
        body = b'{"eventType":"Download"}'
        h = _FakeHandler(body)  # no signature header
        with mock.patch.dict("os.environ",
                             {"WEBHOOK_HMAC_SECRET": SECRET}, clear=False):
            _, ok = self.v.verify_and_parse(h)
        self.assertFalse(ok)

    def test_malformed_header_rejected(self):
        body = b'{"eventType":"Download"}'
        h = _FakeHandler(
            body, {"X-Hub-Signature-256": "notashaprefix"},
        )
        with mock.patch.dict("os.environ",
                             {"WEBHOOK_HMAC_SECRET": SECRET}, clear=False):
            _, ok = self.v.verify_and_parse(h)
        self.assertFalse(ok)

    def test_wrong_secret_produces_wrong_sig_rejected(self):
        body = b'{"eventType":"Download"}'
        h = _FakeHandler(
            body, {"X-Hub-Signature-256": _sig_for(body, "wrong-secret")},
        )
        with mock.patch.dict("os.environ",
                             {"WEBHOOK_HMAC_SECRET": SECRET}, clear=False):
            _, ok = self.v.verify_and_parse(h)
        self.assertFalse(ok)

    def test_tampered_body_rejected(self):
        """Attacker re-signs a different body: must still fail (they'd
        need the secret to compute a valid sig for the new body)."""
        original = b'{"eventType":"Download"}'
        sig = _sig_for(original)
        tampered = b'{"eventType":"Malicious"}'
        h = _FakeHandler(
            tampered, {"X-Hub-Signature-256": sig},
        )
        with mock.patch.dict("os.environ",
                             {"WEBHOOK_HMAC_SECRET": SECRET}, clear=False):
            _, ok = self.v.verify_and_parse(h)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
