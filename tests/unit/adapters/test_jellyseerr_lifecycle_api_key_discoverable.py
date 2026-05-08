"""ADR-0005 Phase 5c.1 (wide) — JellyseerrLifecycle.{probe,ensure}_api_key_discoverable.

Pin the lifecycle-method dispatch shape for the Jellyseerr api-key
promise. Mirrors the existing
``test_jellyseerr_lifecycle_config_wiring.py`` convention.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from media_stack.adapters.jellyseerr.lifecycle import JellyseerrLifecycle
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)


def _ctx(
    *,
    config: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> OrchestrationContext:
    return OrchestrationContext(
        service_id="jellyseerr",
        config=config or {},
        secrets=secrets or {},
        extra=extra or {},
        now=lambda: 0.0,
    )


class JellyseerrLifecycleApiKeyDelegationTests(unittest.TestCase):
    def test_probe_delegates_to_module_singleton(self) -> None:
        life = JellyseerrLifecycle()
        with mock.patch(
            "media_stack.adapters.jellyseerr.lifecycle._API_KEY_DISCOVERABLE_WIRER"
        ) as wirer:
            wirer.probe.return_value = ProbeResult.ok(
                "stub", evidence={}, evaluated_at=0.0,
            )
            life.probe_api_key_discoverable(_ctx())
        wirer.probe.assert_called_once()

    def test_ensure_delegates_to_module_singleton(self) -> None:
        life = JellyseerrLifecycle()
        with mock.patch(
            "media_stack.adapters.jellyseerr.lifecycle._API_KEY_DISCOVERABLE_WIRER"
        ) as wirer:
            wirer.ensure.return_value = Outcome.success(None)
            life.ensure_api_key_discoverable(_ctx())
        wirer.ensure.assert_called_once()


class JellyseerrApiKeyWirerProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        from media_stack.adapters.jellyseerr.api_key_wiring import (
            JellyseerrApiKeyDiscoverableWirer,
        )
        self.Wirer = JellyseerrApiKeyDiscoverableWirer

    def test_probe_failed_when_no_env_no_disk(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JELLYSEERR_API_KEY", None)
            os.environ.pop("CONFIG_ROOT", None)
            result = wirer.probe(ctx)
        self.assertEqual(result.status, "failed")

    def test_probe_ok_when_env_set_service_unreachable(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(
            config={"host": "jellyseerr", "port": 5055},
            secrets={"JELLYSEERR_API_KEY": "abc"},
        )
        with mock.patch.object(
            wirer, "_http_validate", return_value="unreachable",
        ):
            result = wirer.probe(ctx)
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.evidence.get("http_validated"))

    def test_probe_ok_when_env_set_and_http_validates(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(
            config={"host": "jellyseerr", "port": 5055},
            secrets={"JELLYSEERR_API_KEY": "abc"},
        )
        with mock.patch.object(
            wirer, "_http_validate", return_value="ok",
        ):
            result = wirer.probe(ctx)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.evidence.get("http_validated"))

    def test_probe_failed_when_key_rejected(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(
            config={"host": "jellyseerr", "port": 5055},
            secrets={"JELLYSEERR_API_KEY": "stale"},
        )
        with mock.patch.object(
            wirer, "_http_validate", return_value="auth_failed",
        ):
            result = wirer.probe(ctx)
        self.assertEqual(result.status, "failed")


class JellyseerrApiKeyWirerEnsureTests(unittest.TestCase):
    def setUp(self) -> None:
        from media_stack.adapters.jellyseerr.api_key_wiring import (
            JellyseerrApiKeyDiscoverableWirer,
        )
        self.Wirer = JellyseerrApiKeyDiscoverableWirer

    def test_ensure_idempotent_when_env_already_set(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(secrets={"JELLYSEERR_API_KEY": "already-here"})
        with mock.patch.object(
            wirer, "_persist_to_secret", return_value={"status": "ok"},
        ):
            outcome = wirer.ensure(ctx)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.evidence.get("reason"), "already_in_env")

    def test_ensure_transient_when_settings_json_missing(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(
            config={
                "api_key_config": "jellyseerr/settings.json",
                "config_root": "/no-such-root",
            },
            secrets={},
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JELLYSEERR_API_KEY", None)
            outcome = wirer.ensure(ctx)
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.transient)

    def test_ensure_permanent_when_no_config_root(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(secrets={})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JELLYSEERR_API_KEY", None)
            os.environ.pop("CONFIG_ROOT", None)
            outcome = wirer.ensure(ctx)
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.transient)

    def test_ensure_permanent_when_settings_json_has_no_key(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "jellyseerr" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"main": {}}), encoding="utf-8")
            wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
            ctx = _ctx(
                config={
                    "api_key_config": "jellyseerr/settings.json",
                    "config_root": tmp,
                },
                secrets={},
            )
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("JELLYSEERR_API_KEY", None)
                outcome = wirer.ensure(ctx)
            self.assertFalse(outcome.ok)
            self.assertFalse(outcome.transient)

    def test_ensure_success_persists_to_env(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "jellyseerr" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(
                json.dumps({"main": {"apiKey": "discovered"}}),
                encoding="utf-8",
            )
            wirer = self.Wirer(
                key_discoverer=lambda _p, _f: "discovered",
            )
            ctx = _ctx(
                config={
                    "api_key_config": "jellyseerr/settings.json",
                    "config_root": tmp,
                },
                secrets={},
            )
            os.environ.pop("JELLYSEERR_API_KEY", None)
            try:
                with mock.patch.object(
                    wirer, "_persist_to_secret",
                    return_value={"status": "skipped-no-k8s"},
                ):
                    outcome = wirer.ensure(ctx)
                self.assertTrue(outcome.ok)
                self.assertEqual(
                    os.environ.get("JELLYSEERR_API_KEY"), "discovered",
                )
            finally:
                os.environ.pop("JELLYSEERR_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
