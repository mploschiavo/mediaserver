"""ADR-0005 Phase 5c.1 (wide) — ServarrLifecycle.{probe,ensure}_api_key_discoverable.

Pin the lifecycle-method dispatch shape for the four *arr promises
(sonarr / radarr / lidarr / readarr). Mirrors the existing
``test_servarr_lifecycle_download_client.py`` etc convention: one
file per Servarr-family wirer, exercising the ``ServarrLifecycle``
delegators against an in-memory fake of the underlying wirer (so
HTTP / disk are stubbed and only the lifecycle's wiring is under
test).

The wirer itself is exercised against its own fixtures lower in this
file — probe + ensure happy/failure/idempotency paths mirror what
the legacy ``run_preflight`` handler covered.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from media_stack.adapters.servarr.lifecycle import ServarrLifecycle
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)


def _ctx(
    *,
    service_id: str,
    config: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> OrchestrationContext:
    return OrchestrationContext(
        service_id=service_id,
        config=config or {},
        secrets=secrets or {},
        extra=extra or {},
        now=lambda: 0.0,
    )


class ServarrLifecycleApiKeyDiscoverableDelegationTests(unittest.TestCase):
    """The lifecycle methods are thin delegators — they pass
    ``service_id`` + ``ctx`` through to the module-level wirer
    singleton. Tests here pin that wiring so a future refactor
    can't drop the delegation."""

    def test_probe_delegates_with_service_id(self) -> None:
        for sid in ("sonarr", "radarr", "lidarr", "readarr"):
            life = ServarrLifecycle(sid)
            with mock.patch(
                "media_stack.adapters.servarr.lifecycle._API_KEY_DISCOVERABLE_WIRER"
            ) as wirer:
                wirer.probe.return_value = ProbeResult.ok(
                    "stub", evidence={}, evaluated_at=0.0,
                )
                life.probe_api_key_discoverable(_ctx(service_id=sid))
            wirer.probe.assert_called_once()
            args = wirer.probe.call_args
            self.assertEqual(args[0][0], sid)

    def test_ensure_delegates_with_service_id(self) -> None:
        for sid in ("sonarr", "radarr", "lidarr", "readarr"):
            life = ServarrLifecycle(sid)
            with mock.patch(
                "media_stack.adapters.servarr.lifecycle._API_KEY_DISCOVERABLE_WIRER"
            ) as wirer:
                wirer.ensure.return_value = Outcome.success(None)
                life.ensure_api_key_discoverable(_ctx(service_id=sid))
            wirer.ensure.assert_called_once()
            args = wirer.ensure.call_args
            self.assertEqual(args[0][0], sid)


class ServarrApiKeyWirerProbeTests(unittest.TestCase):
    """Probe behaviour: env-or-disk discoverability + optional HTTP
    validation."""

    def setUp(self) -> None:
        from media_stack.adapters.servarr.api_key_wiring import (
            ServarrApiKeyDiscoverableWirer,
        )
        self.Wirer = ServarrApiKeyDiscoverableWirer

    def test_probe_failed_when_no_env_no_disk(self) -> None:
        wirer = self.Wirer(
            key_discoverer=lambda _p, _f: "",
        )
        ctx = _ctx(
            service_id="sonarr",
            config={"api_key_env": "SONARR_API_KEY"},
            secrets={},
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SONARR_API_KEY", None)
            result = wirer.probe("sonarr", ctx)
        self.assertEqual(result.status, "failed")

    def test_probe_ok_when_env_set_service_unreachable(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(
            service_id="sonarr",
            config={
                "api_key_env": "SONARR_API_KEY",
                "host": "sonarr",
                "port": 8989,
            },
            secrets={"SONARR_API_KEY": "abc123"},
        )
        # Force the http_validate branch to return ``unreachable``.
        with mock.patch.object(
            wirer, "_http_validate", return_value="unreachable",
        ):
            result = wirer.probe("sonarr", ctx)
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.evidence.get("http_validated"))

    def test_probe_ok_when_env_set_and_http_validates(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(
            service_id="radarr",
            config={
                "api_key_env": "RADARR_API_KEY",
                "host": "radarr",
                "port": 7878,
            },
            secrets={"RADARR_API_KEY": "abc123"},
        )
        with mock.patch.object(
            wirer, "_http_validate", return_value="ok",
        ):
            result = wirer.probe("radarr", ctx)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.evidence.get("http_validated"))

    def test_probe_failed_when_key_rejected_by_service(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(
            service_id="lidarr",
            config={
                "api_key_env": "LIDARR_API_KEY",
                "host": "lidarr",
                "port": 8686,
            },
            secrets={"LIDARR_API_KEY": "stale-key"},
        )
        with mock.patch.object(
            wirer, "_http_validate", return_value="auth_failed",
        ):
            result = wirer.probe("lidarr", ctx)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.evidence.get("reason"), "auth_failed")

    def test_probe_unsupported_service_id_raises(self) -> None:
        wirer = self.Wirer()
        with self.assertRaises(ValueError):
            wirer.probe("bogus", _ctx(service_id="bogus"))


class ServarrApiKeyWirerEnsureTests(unittest.TestCase):
    def setUp(self) -> None:
        from media_stack.adapters.servarr.api_key_wiring import (
            ServarrApiKeyDiscoverableWirer,
        )
        self.Wirer = ServarrApiKeyDiscoverableWirer

    def test_ensure_idempotent_when_env_already_set(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(
            service_id="sonarr",
            config={"api_key_env": "SONARR_API_KEY"},
            secrets={"SONARR_API_KEY": "already-here"},
        )
        with mock.patch.object(
            wirer, "_persist_to_secret", return_value={"status": "ok"},
        ):
            outcome = wirer.ensure("sonarr", ctx)
        self.assertTrue(outcome.ok)
        self.assertEqual(
            outcome.evidence.get("reason"), "already_in_env",
        )

    def test_ensure_transient_when_config_xml_missing(self, tmp_root: Path | None = None) -> None:
        """Service still warming up — config.xml not yet generated."""
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(
            service_id="sonarr",
            config={
                "api_key_env": "SONARR_API_KEY",
                "api_key_config": "sonarr/config.xml",
                "config_root": "/nonexistent-config-root",
            },
            secrets={},
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SONARR_API_KEY", None)
            outcome = wirer.ensure("sonarr", ctx)
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.transient)

    def test_ensure_permanent_when_no_config_root(self) -> None:
        wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
        ctx = _ctx(
            service_id="readarr",
            config={"api_key_env": "READARR_API_KEY"},
            secrets={},
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("READARR_API_KEY", None)
            os.environ.pop("CONFIG_ROOT", None)
            outcome = wirer.ensure("readarr", ctx)
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.transient)

    def test_ensure_permanent_when_config_xml_has_no_key(self) -> None:
        """File exists but the ``<ApiKey>`` element is empty —
        structural problem, not transient."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "lidarr" / "config.xml"
            cfg_path.parent.mkdir(parents=True)
            cfg_path.write_text("<Config></Config>", encoding="utf-8")
            wirer = self.Wirer(key_discoverer=lambda _p, _f: "")
            ctx = _ctx(
                service_id="lidarr",
                config={
                    "api_key_env": "LIDARR_API_KEY",
                    "api_key_config": "lidarr/config.xml",
                    "config_root": tmp,
                },
                secrets={},
            )
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LIDARR_API_KEY", None)
                outcome = wirer.ensure("lidarr", ctx)
            self.assertFalse(outcome.ok)
            self.assertFalse(outcome.transient)

    def test_ensure_success_persists_to_env(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "radarr" / "config.xml"
            cfg_path.parent.mkdir(parents=True)
            cfg_path.write_text("<Config><ApiKey>k</ApiKey></Config>", encoding="utf-8")
            wirer = self.Wirer(
                key_discoverer=lambda _p, _f: "discovered-key",
            )
            ctx = _ctx(
                service_id="radarr",
                config={
                    "api_key_env": "RADARR_API_KEY",
                    "api_key_config": "radarr/config.xml",
                    "config_root": tmp,
                },
                secrets={},
            )
            os.environ.pop("RADARR_API_KEY", None)
            try:
                with mock.patch.object(
                    wirer, "_persist_to_secret",
                    return_value={"status": "skipped-no-k8s"},
                ):
                    outcome = wirer.ensure("radarr", ctx)
                self.assertTrue(outcome.ok)
                self.assertEqual(
                    os.environ.get("RADARR_API_KEY"), "discovered-key",
                )
            finally:
                os.environ.pop("RADARR_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
