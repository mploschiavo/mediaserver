"""Pin the promise-driven wiring for the ADR-0005 Phase 5c.1 (wide)
api-key-discoverable cutover.

Five new promises (sonarr / radarr / lidarr / readarr / jellyseerr)
each bind to ``{type: lifecycle, service: <svc>, method:
{probe,ensure}_api_key_discoverable}`` so the orchestrator can run
the same probe + ensurer machinery the legacy
``container_preflight_handlers`` ``run_preflight`` invocation used
to. Jellyfin's analogous promise (``jellyfin-api-key-discoverable``)
shipped earlier and is NOT touched by this cutover — it stays on its
own ``probe_has_api_key`` / ``mint_api_key`` shape.

The legacy ``_run_preflights`` function in
``application/jobs/controller_handlers.py`` is GONE — its only
caller (``services/apps/core/job_adapters.py::discover_api_keys``)
now dispatches through ``orchestrator.satisfy_scope([…6 promises])``.

Sections:
  * EachPromiseUsesLifecycleDispatch — probe + ensurer are
    LifecycleProbe + LifecycleEnsurer with the per-service method
    names the lifecycle classes implement.
  * EachPromiseIsBlocking — explicit ``bootstrap_blocking: true``
    so bootstrap waits for each api-key promise before running
    downstream promises that need the key.
  * LegacyRunPreflightsDeleted — ``_run_preflights`` no longer
    importable from the controller_handlers module.
  * DiscoverApiKeysJobScope — the job's promise-id list contains
    every per-service api-key promise.
"""

from __future__ import annotations

import unittest


_EXPECTED_PROMISES = (
    ("sonarr-api-key-discoverable", "sonarr"),
    ("radarr-api-key-discoverable", "radarr"),
    ("lidarr-api-key-discoverable", "lidarr"),
    ("readarr-api-key-discoverable", "readarr"),
    ("jellyseerr-api-key-discoverable", "jellyseerr"),
)


class _LoadedRegistry:
    _cache = None

    @classmethod
    def get(cls):
        if cls._cache is None:
            from media_stack.infrastructure.promises.registry import (
                PromiseRegistryLoader,
            )
            cls._cache = PromiseRegistryLoader().aggregate()
        return cls._cache


class EachPromiseUsesLifecycleDispatch(unittest.TestCase):
    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_probe_is_lifecycle(self) -> None:
        from media_stack.domain.services.promises import LifecycleProbe
        for pid, expected_service in _EXPECTED_PROMISES:
            promise = self.by_id.get(pid)
            self.assertIsNotNone(
                promise, f"{pid!r} dropped out of registry",
            )
            self.assertIsInstance(
                promise.probe, LifecycleProbe,
                f"{pid}: probe regressed from lifecycle dispatch "
                f"(got {type(promise.probe).__name__})",
            )
            self.assertEqual(promise.probe.service, expected_service)
            self.assertEqual(
                promise.probe.method, "probe_api_key_discoverable",
            )

    def test_ensurer_is_job(self) -> None:
        """ADR-0010 Phase 7 — every api-key-discoverable promise
        routes via ``run_job(<service>:ensure-api-key-discoverable)``."""
        from media_stack.domain.services.promises import JobEnsurer
        for pid, expected_service in _EXPECTED_PROMISES:
            promise = self.by_id[pid]
            self.assertIsInstance(
                promise.ensurer, JobEnsurer,
                f"{pid}: ensurer regressed from Job dispatch "
                f"(got {type(promise.ensurer).__name__})",
            )
            self.assertEqual(
                promise.ensurer.job_name,
                f"{expected_service}:ensure-api-key-discoverable",
            )


class EachPromiseIsBlocking(unittest.TestCase):
    def setUp(self) -> None:
        self.by_id = _LoadedRegistry.get().by_id()

    def test_each_promise_is_blocking(self) -> None:
        for pid, _service in _EXPECTED_PROMISES:
            promise = self.by_id[pid]
            self.assertTrue(
                promise.bootstrap_blocking,
                f"{pid}: bootstrap_blocking flipped to False — the "
                f"cutover requires explicit-True so orchestrator-"
                f"driven bootstrap waits for the key to be discoverable.",
            )


class LegacyRunPreflightsDeleted(unittest.TestCase):
    """ADR-0005 Phase 5c.1 wide: ``_run_preflights`` no longer exists.
    A future contributor restoring it would silently skip the
    orchestrator dispatch path; this ratchet pins the deletion."""

    def test_run_preflights_not_importable(self) -> None:
        from media_stack.application.jobs import controller_handlers
        self.assertFalse(
            hasattr(controller_handlers, "_run_preflights"),
            "_run_preflights resurrected — the cutover removed it. "
            "The discover-api-keys job dispatches through "
            "orchestrator.satisfy_scope() instead. Restoring the "
            "legacy function would silently double-up the work.",
        )


class DiscoverApiKeysJobScope(unittest.TestCase):
    """The job's promise-id list is the cutover surface — adding a
    new service's api-key promise without listing it here means
    discover-api-keys silently skips it."""

    def test_job_lists_every_per_service_promise(self) -> None:
        from media_stack.services.apps.core.job_adapters import (
            _DISCOVER_API_KEY_PROMISE_IDS,
        )
        for pid, _service in _EXPECTED_PROMISES:
            self.assertIn(
                pid, _DISCOVER_API_KEY_PROMISE_IDS,
                f"{pid!r} missing from _DISCOVER_API_KEY_PROMISE_IDS — "
                "discover-api-keys won't run its lifecycle ensurer.",
            )

    def test_job_includes_jellyfin_promise(self) -> None:
        """Phase 5b's ``jellyfin-api-key-discoverable`` is the sixth
        member of the scope. Not part of this cutover but the job
        passes it too so the same dispatcher covers Jellyfin."""
        from media_stack.services.apps.core.job_adapters import (
            _DISCOVER_API_KEY_PROMISE_IDS,
        )
        self.assertIn(
            "jellyfin-api-key-discoverable", _DISCOVER_API_KEY_PROMISE_IDS,
        )


if __name__ == "__main__":
    unittest.main()
