"""Tests for ADR-0006 Phase 1 — PromiseRegistryLoader aggregation.

Pin the contract that the orchestrator depends on:

  * Per-service ``plugin.promises:`` blocks load alongside the
    cross-cutting ``contracts/promises/cross_cutting.yaml``.
  * The legacy ``contracts/promises/promises.yaml`` keeps loading
    during the migration grace window (deprecation path; Phase 3
    drops it).
  * Cross-file id-uniqueness is enforced (a per-service entry
    duplicating a cross-cutting id raises with both source paths
    in the message).
  * ``depends_on`` resolution is enforced — every dep must be a
    known promise id in the aggregate registry.
  * Strategy-pattern probe + ensurer parsers handle every existing
    YAML shape without behavioral drift.

Tests construct a ``PromiseRegistryLoader`` with a fake
``ContractsLocator`` + ``yaml_reader`` so behavior is exercised
without touching the real ``contracts/`` tree. Real callers (the
orchestrator, the dispatcher, the operator CLI) accept the
defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from media_stack.domain.services.promises import (
    HttpJsonProbe,
    JobEnsurer,
    LifecycleEnsurer,
    LifecycleProbe,
    PromiseRegistryError,
)
from media_stack.infrastructure.promises.registry import (
    ContractsLocator,
    EnsurerSpecParser,
    ProbeSpecParser,
    PromiseEntryParser,
    PromiseRegistryLoader,
    PromiseRegistryResult,
)


class _FakeLocator:
    """In-memory ContractsLocator stand-in.

    Tests register virtual paths -> fake YAML docs; the loader
    iterates them through the same APIs the real locator exposes."""

    def __init__(
        self,
        *,
        per_service: dict[Path, dict] | None = None,
        cross_cutting: tuple[Path, dict] | None = None,
    ) -> None:
        self._per_service = per_service or {}
        self._cross_cutting = cross_cutting

    def per_service_yamls(self) -> list[Path]:
        return sorted(self._per_service.keys())

    def cross_cutting_yaml(self) -> Path:
        if self._cross_cutting is None:
            return Path("/missing/cross_cutting.yaml")
        return self._cross_cutting[0]

    def docs(self) -> dict[Path, dict]:
        out: dict[Path, dict] = dict(self._per_service)
        if self._cross_cutting is not None:
            out[self._cross_cutting[0]] = self._cross_cutting[1]
        return out


def _yaml_reader_for(docs: dict[Path, dict]):
    """Build a yaml_reader that also makes paths is_file()-true so
    the loader's missing-file warning path doesn't trigger for our
    fake locations."""

    real_is_file = Path.is_file

    def _is_file(self: Path) -> bool:
        if self in docs:
            return True
        return real_is_file(self)

    Path.is_file = _is_file  # type: ignore[method-assign]

    def _read(path: Path) -> Any:
        return docs.get(path, {})

    return _read


@pytest.fixture
def patched_path_is_file(monkeypatch):
    """Restores Path.is_file after each test that mutated it via
    the yaml_reader_for helper above."""
    original = Path.is_file
    yield
    Path.is_file = original  # type: ignore[method-assign]


def _promise_entry(
    pid: str,
    *,
    bootstrap_blocking: bool = True,
    depends_on: list[str] | None = None,
    platforms: list[str] | None = None,
) -> dict:
    return {
        "id": pid,
        "description": f"synthetic {pid}",
        "platforms": platforms or ["compose", "k8s"],
        "bootstrap_blocking": bootstrap_blocking,
        "depends_on": depends_on or [],
        "probe": {
            "type": "http_json",
            "service": pid,
            "path": "/",
            "auth": "none",
            "assert": "True",
        },
        "ensured_by": f"ensure-{pid}",
    }


# ======================================================================
# Aggregator behavior
# ======================================================================


class TestAggregateFromBothSources:
    def test_aggregates_per_service_and_cross_cutting(
        self, patched_path_is_file,
    ) -> None:
        per_service = {
            Path("/contracts/services/jellyfin.yaml"): {
                "plugin": {"promises": [_promise_entry("jellyfin-running")]},
            },
            Path("/contracts/services/sonarr.yaml"): {
                "plugin": {"promises": [_promise_entry("sonarr-has-indexers")]},
            },
        }
        cross = (
            Path("/contracts/promises/cross_cutting.yaml"),
            {"promises": [_promise_entry("gateway-https-listener-up")]},
        )
        locator = _FakeLocator(per_service=per_service, cross_cutting=cross)
        loader = PromiseRegistryLoader(
            locator=locator,
            yaml_reader=_yaml_reader_for(locator.docs()),
        )

        result = loader.aggregate()

        assert isinstance(result, PromiseRegistryResult)
        assert {p.id for p in result.promises} == {
            "jellyfin-running",
            "sonarr-has-indexers",
            "gateway-https-listener-up",
        }

    def test_source_path_map_is_per_id(
        self, patched_path_is_file,
    ) -> None:
        per_service = {
            Path("/contracts/services/jellyfin.yaml"): {
                "plugin": {"promises": [_promise_entry("jellyfin-running")]},
            },
        }
        cross = (
            Path("/contracts/promises/cross_cutting.yaml"),
            {"promises": [_promise_entry("gateway-https-listener-up")]},
        )
        locator = _FakeLocator(per_service=per_service, cross_cutting=cross)
        loader = PromiseRegistryLoader(
            locator=locator,
            yaml_reader=_yaml_reader_for(locator.docs()),
        )

        result = loader.aggregate()

        assert result.source_paths["jellyfin-running"] == Path(
            "/contracts/services/jellyfin.yaml",
        )
        assert result.source_paths["gateway-https-listener-up"] == Path(
            "/contracts/promises/cross_cutting.yaml",
        )

    def test_per_service_only_skips_cross_cutting(
        self, patched_path_is_file,
    ) -> None:
        per_service = {
            Path("/contracts/services/jellyfin.yaml"): {
                "plugin": {"promises": [_promise_entry("jellyfin-running")]},
            },
        }
        cross = (
            Path("/contracts/promises/cross_cutting.yaml"),
            {"promises": [_promise_entry("gateway-https-listener-up")]},
        )
        locator = _FakeLocator(per_service=per_service, cross_cutting=cross)
        loader = PromiseRegistryLoader(
            locator=locator,
            yaml_reader=_yaml_reader_for(locator.docs()),
        )

        result = loader.aggregate(cross_cutting=False)

        assert {p.id for p in result.promises} == {"jellyfin-running"}

    def test_service_yaml_without_promises_block_is_skipped(
        self, patched_path_is_file,
    ) -> None:
        per_service = {
            # Service contract without any plugin.promises block —
            # must NOT raise; just contributes zero entries.
            Path("/contracts/services/qbittorrent.yaml"): {
                "plugin": {"jobs": {"foo": {"handler": "x"}}},
            },
            Path("/contracts/services/jellyfin.yaml"): {
                "plugin": {"promises": [_promise_entry("jellyfin-running")]},
            },
        }
        locator = _FakeLocator(per_service=per_service)
        loader = PromiseRegistryLoader(
            locator=locator,
            yaml_reader=_yaml_reader_for(locator.docs()),
        )

        result = loader.aggregate(cross_cutting=False)

        assert {p.id for p in result.promises} == {"jellyfin-running"}


class TestCrossFileValidation:
    def test_duplicate_id_across_files_raises(
        self, patched_path_is_file,
    ) -> None:
        per_service = {
            Path("/contracts/services/jellyfin.yaml"): {
                "plugin": {"promises": [_promise_entry("jellyfin-running")]},
            },
        }
        cross = (
            Path("/contracts/promises/cross_cutting.yaml"),
            # Duplicate id — same one as per-service.
            {"promises": [_promise_entry("jellyfin-running")]},
        )
        locator = _FakeLocator(per_service=per_service, cross_cutting=cross)
        loader = PromiseRegistryLoader(
            locator=locator,
            yaml_reader=_yaml_reader_for(locator.docs()),
        )

        with pytest.raises(PromiseRegistryError) as excinfo:
            loader.aggregate()
        msg = str(excinfo.value)
        assert "duplicate" in msg.lower()
        assert "jellyfin-running" in msg
        # Both source paths show up in the message so operators can
        # find both edits.
        assert "jellyfin.yaml" in msg
        assert "cross_cutting.yaml" in msg

    def test_unknown_depends_on_raises(
        self, patched_path_is_file,
    ) -> None:
        cross = (
            Path("/contracts/promises/cross_cutting.yaml"),
            {"promises": [
                _promise_entry(
                    "jellyfin-api-key-discoverable",
                    depends_on=["does-not-exist"],
                ),
            ]},
        )
        locator = _FakeLocator(cross_cutting=cross)
        loader = PromiseRegistryLoader(
            locator=locator,
            yaml_reader=_yaml_reader_for(locator.docs()),
        )

        with pytest.raises(PromiseRegistryError) as excinfo:
            loader.aggregate()
        msg = str(excinfo.value)
        assert "depends_on" in msg
        assert "does-not-exist" in msg

    def test_cross_file_depends_on_resolves(
        self, patched_path_is_file,
    ) -> None:
        # Phase 2 will move per-service promises gradually — a
        # promise in the cross-cutting file may depend on a per-
        # service one and vice versa during the transition.
        per_service = {
            Path("/contracts/services/jellyfin.yaml"): {
                "plugin": {"promises": [_promise_entry("jellyfin-running")]},
            },
        }
        cross = (
            Path("/contracts/promises/cross_cutting.yaml"),
            {"promises": [
                _promise_entry(
                    "jellyfin-api-key-discoverable",
                    depends_on=["jellyfin-running"],
                ),
            ]},
        )
        locator = _FakeLocator(per_service=per_service, cross_cutting=cross)
        loader = PromiseRegistryLoader(
            locator=locator,
            yaml_reader=_yaml_reader_for(locator.docs()),
        )

        result = loader.aggregate()

        ids = {p.id for p in result.promises}
        assert ids == {"jellyfin-running", "jellyfin-api-key-discoverable"}


# ======================================================================
# Backwards-compat surface
# ======================================================================


class TestBackwardsCompatShims:
    def test_load_registry_with_path_falls_back_to_single_file(
        self, tmp_path,
    ) -> None:
        # Existing test fixtures pass a path to a single YAML — the
        # shim must keep working with that signature.
        from media_stack.infrastructure.promises.registry import (
            load_registry,
        )

        registry_path = tmp_path / "promises.yaml"
        registry_path.write_text(
            "promises:\n"
            "  - id: alpha\n"
            "    description: one\n"
            "    platforms: [compose]\n"
            "    probe:\n"
            "      type: http_json\n"
            "      service: alpha\n"
            "      path: /\n"
            "      assert: True\n"
            "    ensured_by: ensure-alpha\n",
            encoding="utf-8",
        )

        result = load_registry(registry_path)

        assert len(result) == 1
        assert result[0].id == "alpha"

    def test_real_aggregate_loads_against_committed_registry(self) -> None:
        # Smoke test — the real loader against the real contracts/
        # tree should return a non-empty list and validate cleanly.
        # Pins that ADR-0006 Phase 1 doesn't break the existing
        # registry on day-one.
        loader = PromiseRegistryLoader()
        result = loader.aggregate()
        assert len(result.promises) > 0
        # The Jellyfin family from ADR-0005 Phase 2 should show up.
        ids = {p.id for p in result.promises}
        assert "jellyfin-running" in ids
        assert "jellyfin-api-key-discoverable" in ids
        assert "jellyfin-libraries" in ids


# ======================================================================
# Strategy-parser unit coverage (probe + ensurer dispatch)
# ======================================================================


class TestProbeSpecParser:
    def test_lifecycle_probe(self) -> None:
        parser = ProbeSpecParser()
        result = parser.parse("p", {
            "type": "lifecycle",
            "service": "jellyfin",
            "method": "probe_running",
        })
        assert isinstance(result, LifecycleProbe)
        assert result.service == "jellyfin"
        assert result.method == "probe_running"

    def test_http_json_probe(self) -> None:
        parser = ProbeSpecParser()
        result = parser.parse("p", {
            "type": "http_json",
            "service": "sonarr",
            "path": "/api/v3/system/status",
            "auth": "api_key",
            "assert": "response is not None",
        })
        assert isinstance(result, HttpJsonProbe)
        assert result.service == "sonarr"
        assert result.auth == "api_key"

    def test_unknown_probe_type_lists_known_types(self) -> None:
        parser = ProbeSpecParser()
        with pytest.raises(PromiseRegistryError) as excinfo:
            parser.parse("p", {"type": "telepathy"})
        msg = str(excinfo.value)
        assert "telepathy" in msg
        # The error should help the author discover the typo by
        # listing valid kinds.
        assert "lifecycle" in msg
        assert "http_json" in msg


class TestEnsurerSpecParser:
    def test_bare_string_resolves_to_job(self) -> None:
        parser = EnsurerSpecParser()
        result = parser.parse("p", "ensure-jellyfin-libraries")
        assert isinstance(result, JobEnsurer)
        assert result.job_name == "ensure-jellyfin-libraries"

    def test_bare_string_in_infra_vocabulary(self) -> None:
        from media_stack.domain.services.promises import InfraEnsurer
        parser = EnsurerSpecParser()
        result = parser.parse("p", "kubectl-apply")
        assert isinstance(result, InfraEnsurer)
        assert result.operator == "kubectl-apply"

    def test_typed_lifecycle(self) -> None:
        parser = EnsurerSpecParser()
        result = parser.parse("p", {
            "type": "lifecycle",
            "service": "jellyfin",
            "method": "mint_api_key",
        })
        assert isinstance(result, LifecycleEnsurer)
        assert result.method == "mint_api_key"

    def test_unknown_typed_dispatch_lists_known_types(self) -> None:
        parser = EnsurerSpecParser()
        with pytest.raises(PromiseRegistryError) as excinfo:
            parser.parse("p", {"type": "magic"})
        msg = str(excinfo.value)
        assert "magic" in msg
        assert "lifecycle" in msg
        assert "job" in msg


class TestPromiseEntryParserComposition:
    def test_swappable_probe_parser(self) -> None:
        # Composition (not inheritance) — tests can sub a fake
        # probe parser to assert PromiseEntryParser doesn't read
        # ``probe`` directly.
        sentinel_probe = LifecycleProbe(service="x", method="y")

        class _FakeProbeParser:
            def parse(self, pid: str, raw: Any) -> Any:
                return sentinel_probe

        parser = PromiseEntryParser(probe_parser=_FakeProbeParser())
        promise = parser.parse({
            "id": "p",
            "description": "",
            "platforms": ["compose"],
            "probe": {"type": "ignored-by-fake"},
            "ensured_by": "ensure-p",
        })
        assert promise.probe is sentinel_probe


# ======================================================================
# ContractsLocator
# ======================================================================


class TestContractsLocator:
    def test_env_override_wins(self, tmp_path) -> None:
        env = {"MEDIA_STACK_CONTRACTS_ROOT": str(tmp_path)}
        locator = ContractsLocator(env_provider=env)
        assert locator.root() == tmp_path
        assert locator.legacy_promises_yaml() == (
            tmp_path / "promises" / "promises.yaml"
        )

    def test_candidate_walk_picks_first_existing(
        self, tmp_path,
    ) -> None:
        # Create a contracts dir structure and verify the locator
        # finds it.
        contracts = tmp_path / "contracts"
        (contracts / "services").mkdir(parents=True)
        (contracts / "promises").mkdir()
        locator = ContractsLocator(
            env_provider={},
            package_path=tmp_path,
        )
        assert locator.root() == contracts

    def test_cross_cutting_falls_back_to_legacy_filename(
        self, tmp_path,
    ) -> None:
        contracts = tmp_path / "contracts"
        promises_dir = contracts / "promises"
        promises_dir.mkdir(parents=True)
        # Only the legacy filename exists.
        (promises_dir / "promises.yaml").write_text(
            "promises: []\n", encoding="utf-8",
        )
        (contracts / "services").mkdir()
        locator = ContractsLocator(
            env_provider={},
            package_path=tmp_path,
        )
        assert locator.cross_cutting_yaml() == (
            promises_dir / "promises.yaml"
        )

    def test_cross_cutting_prefers_new_filename_when_present(
        self, tmp_path,
    ) -> None:
        contracts = tmp_path / "contracts"
        promises_dir = contracts / "promises"
        promises_dir.mkdir(parents=True)
        (promises_dir / "cross_cutting.yaml").write_text(
            "promises: []\n", encoding="utf-8",
        )
        (promises_dir / "promises.yaml").write_text(
            "promises: []\n", encoding="utf-8",
        )
        (contracts / "services").mkdir()
        locator = ContractsLocator(
            env_provider={},
            package_path=tmp_path,
        )
        assert locator.cross_cutting_yaml() == (
            promises_dir / "cross_cutting.yaml"
        )
