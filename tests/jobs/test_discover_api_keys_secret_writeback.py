"""Ratchet #4 — bootstrap → K8s Secret round-trip.

The discover-api-keys job harvests keys from on-disk config files
and patches them into the ``media-stack-secrets`` Secret. The bug
class to lock out: an empty / missing key clobbering a previously-
populated value, leaving the secret with an empty string and the
controller silently sending blank credentials.

We mock the K8s ``CoreV1Api`` and assert:

1. ``patch_namespaced_secret`` is invoked with secret name
   ``media-stack-secrets`` in namespace ``media-stack``.
2. The body contains every discovered key, base64-encoded per
   Secret semantics.
3. Empty / missing keys do NOT overwrite the existing populated
   value (idempotent).
"""

from __future__ import annotations

import base64
import os
import sys
import unittest
import unittest.mock as _mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def _decoded_data(call) -> dict[str, str]:
    """Pull the ``data`` field out of a ``patch_namespaced_secret``
    mock call and base64-decode every value."""
    kwargs = call.kwargs or {}
    body = kwargs.get("body") or (call.args[2] if len(call.args) > 2 else {})
    data = (body or {}).get("data") or {}
    out: dict[str, str] = {}
    for k, v in data.items():
        try:
            out[k] = base64.b64decode(v).decode("utf-8")
        except Exception:
            out[k] = ""
    return out


class _StubState:
    """Stand-in for the bootstrap state object the persist helper
    pulls ``preflight_results`` from."""

    def __init__(self, preflight_results: dict | None = None) -> None:
        self.preflight_results = preflight_results or {}


class DiscoverApiKeysSecretWritebackTests(unittest.TestCase):

    def _patch_k8s(self):
        """Install fake ``kubernetes.client`` + ``config`` modules so
        the helper finds them without needing the real package."""
        # Build fake modules and inject into sys.modules.
        fake_v1 = _mock.MagicMock()

        fake_client_mod = _mock.MagicMock()
        fake_client_mod.CoreV1Api = _mock.MagicMock(return_value=fake_v1)

        fake_config_mod = _mock.MagicMock()

        # ``ConfigException`` must exist so the helper's
        # ``try/except config.ConfigException`` works.
        class _ConfigEx(Exception):
            pass
        fake_config_mod.ConfigException = _ConfigEx
        fake_config_mod.load_incluster_config = _mock.MagicMock()
        fake_config_mod.load_kube_config = _mock.MagicMock()

        fake_kubernetes = _mock.MagicMock()
        fake_kubernetes.client = fake_client_mod
        fake_kubernetes.config = fake_config_mod

        return fake_v1, _mock.patch.dict(
            sys.modules,
            {
                "kubernetes": fake_kubernetes,
                "kubernetes.client": fake_client_mod,
                "kubernetes.config": fake_config_mod,
            },
        )

    def test_writes_discovered_keys_base64_into_secret(self) -> None:
        from media_stack.cli.commands import controller_k8s
        fake_v1, k8s_patch = self._patch_k8s()
        env = {
            "K8S_NAMESPACE": "media-stack",
            "K8S_SECRET_NAME": "media-stack-secrets",
            # Pretend bootstrap already exported these.
            "SONARR_API_KEY": "sonarrkey",
            "RADARR_API_KEY": "radarrkey",
        }
        # Patch SERVICES so we have a deterministic set with
        # api_key_env declared.
        fake_services = [
            _mock.MagicMock(id="sonarr", api_key_env="SONARR_API_KEY",
                            user_id_env=""),
            _mock.MagicMock(id="radarr", api_key_env="RADARR_API_KEY",
                            user_id_env=""),
            _mock.MagicMock(id="jellyfin", api_key_env="JELLYFIN_API_KEY",
                            user_id_env="JELLYFIN_USER_ID"),
        ]
        with _mock.patch.dict(os.environ, env, clear=False), \
                _mock.patch(
                    "media_stack.api.services.registry.SERVICES",
                    fake_services), \
                k8s_patch:
            state = _StubState(preflight_results={
                "jellyfin": {"JELLYFIN_API_KEY": "jfkey"},
            })
            controller_k8s._persist_preflight_keys_to_secret(state)

        # Patch must have fired exactly once.
        self.assertEqual(
            fake_v1.patch_namespaced_secret.call_count, 1,
            "expected a single patch_namespaced_secret call",
        )
        call = fake_v1.patch_namespaced_secret.call_args
        # Secret name + namespace assertions (positional or kw).
        kw = call.kwargs
        self.assertEqual(kw.get("name"), "media-stack-secrets")
        self.assertEqual(kw.get("namespace"), "media-stack")

        decoded = _decoded_data(call)
        # All three keys made it into the Secret, base64-decoded.
        self.assertEqual(decoded.get("SONARR_API_KEY"), "sonarrkey")
        self.assertEqual(decoded.get("RADARR_API_KEY"), "radarrkey")
        self.assertEqual(decoded.get("JELLYFIN_API_KEY"), "jfkey")

    def test_empty_or_missing_keys_do_not_overwrite(self) -> None:
        """Idempotency: a discovery pass that finds nothing for
        ``JELLYFIN_API_KEY`` must not patch an empty value back into
        the Secret. The current implementation achieves this by
        excluding empty values from the patch body entirely — we
        lock that behaviour."""
        from media_stack.cli.commands import controller_k8s
        fake_v1, k8s_patch = self._patch_k8s()
        env = {
            "K8S_NAMESPACE": "media-stack",
            "K8S_SECRET_NAME": "media-stack-secrets",
            "SONARR_API_KEY": "sonarrkey",  # populated
            "JELLYFIN_API_KEY": "",          # explicitly empty
        }
        fake_services = [
            _mock.MagicMock(id="sonarr", api_key_env="SONARR_API_KEY",
                            user_id_env=""),
            _mock.MagicMock(id="jellyfin", api_key_env="JELLYFIN_API_KEY",
                            user_id_env=""),
        ]
        with _mock.patch.dict(os.environ, env, clear=False), \
                _mock.patch(
                    "media_stack.api.services.registry.SERVICES",
                    fake_services), \
                k8s_patch:
            controller_k8s._persist_preflight_keys_to_secret(_StubState())

        self.assertEqual(fake_v1.patch_namespaced_secret.call_count, 1)
        decoded = _decoded_data(fake_v1.patch_namespaced_secret.call_args)
        self.assertIn("SONARR_API_KEY", decoded)
        self.assertNotIn(
            "JELLYFIN_API_KEY", decoded,
            "empty discovery must not overwrite a populated key",
        )

    def test_no_discoveries_skips_patch_entirely(self) -> None:
        """If discovery harvested nothing, do NOT issue a patch with
        an empty data dict — that would clobber every key in the
        Secret depending on the merge semantics."""
        from media_stack.cli.commands import controller_k8s
        fake_v1, k8s_patch = self._patch_k8s()
        env = {
            "K8S_NAMESPACE": "media-stack",
            "K8S_SECRET_NAME": "media-stack-secrets",
        }
        with _mock.patch.dict(os.environ, env, clear=True), \
                _mock.patch(
                    "media_stack.api.services.registry.SERVICES",
                    []), \
                k8s_patch:
            controller_k8s._persist_preflight_keys_to_secret(_StubState())

        fake_v1.patch_namespaced_secret.assert_not_called()


if __name__ == "__main__":
    unittest.main()
