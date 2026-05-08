"""Tests for the ``discover-api-keys`` job adapter.

The bug we're guarding against (regression-test style):

- Pre-fix: the job called the legacy ``_run_preflights`` then
  ``_persist_preflight_keys_to_secret``. If a single preflight raised
  (Jellyfin not yet bootstrapped on a fresh stack), the K8s client
  failed (RBAC missing the ``patch`` verb), or the state stub didn't
  populate ``preflight_results``, the whole job ended with
  ``status: error`` and the K8s ``media-stack-secrets`` stayed full of
  empty strings — every endpoint that did
  ``os.environ.get("JELLYFIN_API_KEY", "")`` saw "" and returned an
  empty payload.
- ADR-0005 Phase 5c.1 wide cutover: ``_run_preflights`` is GONE.
  The job dispatches every service's api-key promise through
  ``orchestrator.satisfy_scope([…6 promises])``. Per-service
  transient failures (e.g. config.xml not yet generated) land as
  skip entries. The on-disk fallback (``_harvest_keys_from_disk``)
  + k8s secret patch survive — they're post-discovery actions.
- Post-fix: per-service failures are recorded as skips, the job
  always walks every service's on-disk config file as the canonical
  source of truth, secret-write failures are reported but don't
  abort, and the response carries ``discovered`` + ``skipped`` +
  ``persist`` for the dashboard to surface.

Coverage:
- success path harvests every service's key
- partial-skip: one service's file is missing, others succeed
- ``orchestrator.satisfy_scope`` raising doesn't abort the job
- ``orchestrator.satisfy_scope`` is called with the six expected promise ids
- secret write happens via the K8s client when ``K8S_NAMESPACE`` is set
- compose mode (no ``K8S_NAMESPACE``) returns ``skipped-no-k8s``
- previous env value is preserved when the file is unreadable in this run
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.core import job_adapters as ja  # noqa: E402


class _FakeCtx:
    config_root = "/srv-config"
    wait_timeout = 60
    cancelled = False


class HarvestKeysFromDiskTests(unittest.TestCase):
    def test_success_path_collects_every_key(self) -> None:
        from media_stack.api.services.registry import ServiceDef

        services = [
            ServiceDef(id="sonarr", name="Sonarr", api_key_env="SONARR_API_KEY"),
            ServiceDef(id="radarr", name="Radarr", api_key_env="RADARR_API_KEY"),
        ]
        with mock.patch(
            "media_stack.api.services.registry.SERVICES", services,
        ), mock.patch(
            "media_stack.api.services.registry.read_api_key_from_file",
            side_effect=lambda sid, _root: f"{sid}-key",
        ):
            discovered, skipped = ja._harvest_keys_from_disk("/srv-config")
        self.assertEqual(
            discovered,
            {"SONARR_API_KEY": "sonarr-key", "RADARR_API_KEY": "radarr-key"},
        )
        self.assertEqual(skipped, [])

    def test_partial_skip_when_one_file_missing(self) -> None:
        """Sonarr has a key on disk; Radarr doesn't — the job should
        return Sonarr's key and record Radarr as skipped instead of
        erroring the whole sweep."""
        from media_stack.api.services.registry import ServiceDef

        services = [
            ServiceDef(id="sonarr", name="Sonarr", api_key_env="SONARR_API_KEY"),
            ServiceDef(id="radarr", name="Radarr", api_key_env="RADARR_API_KEY"),
        ]
        with mock.patch(
            "media_stack.api.services.registry.SERVICES", services,
        ), mock.patch(
            "media_stack.api.services.registry.read_api_key_from_file",
            side_effect=lambda sid, _root: "sonarr-key" if sid == "sonarr" else "",
        ), mock.patch.dict(os.environ, {"RADARR_API_KEY": ""}, clear=False):
            os.environ.pop("RADARR_API_KEY", None)
            discovered, skipped = ja._harvest_keys_from_disk("/srv-config")
        self.assertEqual(discovered, {"SONARR_API_KEY": "sonarr-key"})
        self.assertTrue(any("radarr" in s for s in skipped))

    def test_preserves_previous_env_value_when_file_unreadable(self) -> None:
        """If the PVC is momentarily unreadable but the runtime env
        already has a known-good key, we MUST NOT overwrite it with
        empty — would cause a flap on every reconcile."""
        from media_stack.api.services.registry import ServiceDef

        services = [
            ServiceDef(id="sonarr", name="Sonarr", api_key_env="SONARR_API_KEY"),
        ]
        with mock.patch(
            "media_stack.api.services.registry.SERVICES", services,
        ), mock.patch(
            "media_stack.api.services.registry.read_api_key_from_file",
            return_value="",
        ), mock.patch.dict(
            os.environ, {"SONARR_API_KEY": "previously-known-good"}, clear=False,
        ):
            discovered, skipped = ja._harvest_keys_from_disk("/srv-config")
        self.assertEqual(discovered, {"SONARR_API_KEY": "previously-known-good"})
        self.assertTrue(any("kept env value" in s for s in skipped))

    def test_jellyfin_not_bootstrapped_is_skip_not_error(self) -> None:
        """Jellyfin's key lives in SQLite and the DB doesn't exist on
        a fresh stack — that's a per-service skip, not a hard error."""
        from media_stack.api.services.registry import ServiceDef

        services = [
            ServiceDef(id="jellyfin", name="Jellyfin", api_key_env="JELLYFIN_API_KEY"),
        ]
        with mock.patch(
            "media_stack.api.services.registry.SERVICES", services,
        ), mock.patch(
            "media_stack.api.services.registry.read_api_key_from_file",
            return_value="",
        ), mock.patch(
            "media_stack.services.apps.jellyfin.api_key_db.read_jellyfin_api_key_from_db",
            return_value=("", "missing"),
        ), mock.patch.dict(os.environ, {"JELLYFIN_API_KEY": ""}, clear=False):
            os.environ.pop("JELLYFIN_API_KEY", None)
            discovered, skipped = ja._harvest_keys_from_disk("/srv-config")
        self.assertEqual(discovered, {})
        self.assertTrue(any("jellyfin" in s and "not bootstrapped" in s for s in skipped))

    def test_parse_failure_is_skip_not_error(self) -> None:
        """A corrupt config file should not abort the whole sweep."""
        from media_stack.api.services.registry import ServiceDef

        services = [
            ServiceDef(id="sonarr", name="Sonarr", api_key_env="SONARR_API_KEY"),
        ]
        with mock.patch(
            "media_stack.api.services.registry.SERVICES", services,
        ), mock.patch(
            "media_stack.api.services.registry.read_api_key_from_file",
            side_effect=ValueError("malformed XML"),
        ), mock.patch.dict(os.environ, {"SONARR_API_KEY": ""}, clear=False):
            os.environ.pop("SONARR_API_KEY", None)
            discovered, skipped = ja._harvest_keys_from_disk("/srv-config")
        self.assertEqual(discovered, {})
        self.assertTrue(any("parse-failed" in s for s in skipped))


class PersistKeysToSecretSafeTests(unittest.TestCase):
    def test_returns_skipped_when_no_namespace(self) -> None:
        with mock.patch.dict(os.environ, {"K8S_NAMESPACE": ""}, clear=False):
            os.environ.pop("K8S_NAMESPACE", None)
            result = ja._persist_preflight_keys_to_secret_safe(
                object(), {"SONARR_API_KEY": "x"},
            )
        self.assertEqual(result["status"], "skipped-no-k8s")

    def test_returns_skipped_when_no_keys(self) -> None:
        with mock.patch.dict(
            os.environ, {"K8S_NAMESPACE": "media-stack"}, clear=False,
        ):
            result = ja._persist_preflight_keys_to_secret_safe(object(), {})
        self.assertEqual(result["status"], "skipped-empty")

    def test_calls_k8s_client_with_base64_encoded_data(self) -> None:
        fake_v1 = mock.MagicMock()
        fake_client_module = mock.MagicMock()
        fake_client_module.CoreV1Api.return_value = fake_v1
        fake_config_module = mock.MagicMock()

        with mock.patch.dict(
            os.environ, {"K8S_NAMESPACE": "media-stack"}, clear=False,
        ), mock.patch.dict(
            sys.modules,
            {
                "kubernetes": mock.MagicMock(client=fake_client_module, config=fake_config_module),
            },
        ):
            result = ja._persist_preflight_keys_to_secret_safe(
                object(), {"SONARR_API_KEY": "abc123"},
            )
        self.assertEqual(result["status"], "ok")
        self.assertIn("SONARR_API_KEY", result["written"])
        fake_v1.patch_namespaced_secret.assert_called_once()
        # Verify body was base64-encoded
        kwargs = fake_v1.patch_namespaced_secret.call_args.kwargs
        body = kwargs.get("body") or fake_v1.patch_namespaced_secret.call_args.args[2]
        import base64
        self.assertEqual(
            body["data"]["SONARR_API_KEY"],
            base64.b64encode(b"abc123").decode(),
        )

    def test_rbac_403_reported_distinctly(self) -> None:
        """RBAC denials are actionable (re-apply controller.yaml); we
        give them their own status string so the dashboard can render
        a specific call-to-action."""
        fake_v1 = mock.MagicMock()
        fake_v1.patch_namespaced_secret.side_effect = Exception(
            'HTTP response body: {"reason":"Forbidden"}, status: 403',
        )
        fake_client_module = mock.MagicMock()
        fake_client_module.CoreV1Api.return_value = fake_v1
        fake_config_module = mock.MagicMock()

        with mock.patch.dict(
            os.environ, {"K8S_NAMESPACE": "media-stack"}, clear=False,
        ), mock.patch.dict(
            sys.modules,
            {
                "kubernetes": mock.MagicMock(client=fake_client_module, config=fake_config_module),
            },
        ):
            result = ja._persist_preflight_keys_to_secret_safe(
                object(), {"SONARR_API_KEY": "abc"},
            )
        self.assertEqual(result["status"], "rbac-denied")


class _StubOrchestrator:
    """Simple stub for ``PromiseOrchestrator`` — captures the
    ``satisfy_scope`` call so tests can assert against it without
    mocking the (heavy) lifecycle dispatch chain. Returns whatever
    ``TickSummary`` the fixture supplies."""

    def __init__(self, summary, *, raise_exc: Exception | None = None) -> None:
        self._summary = summary
        self._raise = raise_exc
        self.calls: list[dict] = []

    def satisfy_scope(self, promise_ids, **kwargs):
        self.calls.append({"promise_ids": list(promise_ids), **kwargs})
        if self._raise is not None:
            raise self._raise
        return self._summary


def _empty_summary():
    from media_stack.domain.services.promises import TickSummary
    return TickSummary.empty(started_at=0.0)


class DiscoverApiKeysJobTests(unittest.TestCase):
    """Top-level job behaviour — ADR-0005 Phase 5c.1 wide cutover."""

    def test_satisfy_scope_failure_does_not_abort_job(self) -> None:
        """A bug in the orchestrator used to surface as
        ``status: error`` in /api/jobs.history (when the legacy
        ``_run_preflights`` raised) — now it lands as a recorded skip
        and the on-disk fallback still resolves keys."""
        stub = _StubOrchestrator(
            _empty_summary(), raise_exc=RuntimeError("boom"),
        )
        with mock.patch(
            "media_stack.application.services.orchestrator.PromiseOrchestrator",
            return_value=stub,
        ), mock.patch(
            "media_stack.services.apps.core.job_adapters._harvest_keys_from_disk",
            return_value=({"SONARR_API_KEY": "k"}, []),
        ), mock.patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            return_value={"status": "skipped-no-k8s", "written": []},
        ):
            result = ja.discover_api_keys(_FakeCtx())

        self.assertEqual(result["action"], "discover-api-keys")
        self.assertIn("SONARR_API_KEY", result["discovered"])
        self.assertTrue(
            any("orchestrator.satisfy_scope" in s for s in result["skipped"]),
            f"expected orchestrator.satisfy_scope skip, got {result['skipped']!r}",
        )

    def test_dispatches_six_per_service_promises(self) -> None:
        """The wide cutover passes one promise id per service whose
        admin API key the controller wants discoverable. Adding a new
        service to the list is the cutover surface — pinning here
        catches a silent regression that drops a service."""
        stub = _StubOrchestrator(_empty_summary())
        with mock.patch(
            "media_stack.application.services.orchestrator.PromiseOrchestrator",
            return_value=stub,
        ), mock.patch(
            "media_stack.services.apps.core.job_adapters._harvest_keys_from_disk",
            return_value=({}, []),
        ), mock.patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            return_value={"status": "skipped-no-k8s", "written": []},
        ):
            ja.discover_api_keys(_FakeCtx())

        self.assertEqual(len(stub.calls), 1)
        ids = stub.calls[0]["promise_ids"]
        for expected in (
            "jellyfin-api-key-discoverable",
            "sonarr-api-key-discoverable",
            "radarr-api-key-discoverable",
            "lidarr-api-key-discoverable",
            "readarr-api-key-discoverable",
            "jellyseerr-api-key-discoverable",
        ):
            self.assertIn(expected, ids)

    def test_returns_structured_summary(self) -> None:
        """The dashboard needs ``discovered`` + ``skipped`` + ``persist``
        to tell the operator what worked, what didn't, and what to do
        about it."""
        stub = _StubOrchestrator(_empty_summary())
        with mock.patch(
            "media_stack.application.services.orchestrator.PromiseOrchestrator",
            return_value=stub,
        ), mock.patch(
            "media_stack.services.apps.core.job_adapters._harvest_keys_from_disk",
            return_value=({"SONARR_API_KEY": "k"}, ["radarr: no key on disk"]),
        ), mock.patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            return_value={"status": "ok", "written": ["SONARR_API_KEY"]},
        ):
            result = ja.discover_api_keys(_FakeCtx())

        self.assertEqual(result["discovered"], ["SONARR_API_KEY"])
        self.assertEqual(result["skipped"], ["radarr: no key on disk"])
        self.assertEqual(result["persist"]["status"], "ok")

    def test_invalidates_runtime_keys_cache_after_persist(self) -> None:
        """Otherwise the next /api/libraries call would still see the
        stale (None) cache entry until the 30s TTL elapsed."""
        stub = _StubOrchestrator(_empty_summary())
        with mock.patch(
            "media_stack.application.services.orchestrator.PromiseOrchestrator",
            return_value=stub,
        ), mock.patch(
            "media_stack.services.apps.core.job_adapters._harvest_keys_from_disk",
            return_value=({}, []),
        ), mock.patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            return_value={"status": "skipped-no-k8s", "written": []},
        ), mock.patch(
            "media_stack.api.services.runtime_keys.invalidate_cache",
        ) as m_inv:
            ja.discover_api_keys(_FakeCtx())
        m_inv.assert_called_once()


if __name__ == "__main__":
    unittest.main()
