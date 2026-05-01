"""Tests for the promise registry YAML loader — ADR-0003 Phase 4a.

Pin both shapes the loader handles:

  * Legacy schema (string ``ensured_by``) → ``JobEnsurer`` /
    ``InfraEnsurer`` based on whether the string is in the infra
    vocabulary.
  * New schema (dict ``ensured_by`` with ``type:`` discriminator)
    → ``LifecycleEnsurer`` / ``JobEnsurer`` / ``DeployEnsurer`` /
    ``InfraEnsurer``.

Probe types parse the same way: every existing probe type
(``http_json``, ``file_json``, ``k8s_resource``, …) plus the new
``lifecycle`` type.

Errors carry the offending promise id + a one-line reason — so a
typo in the YAML produces an actionable message, not a deep
``KeyError`` from the loader internals.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_stack.domain.services.promises import (
    DeployEnsurer,
    HttpJsonProbe,
    InfraEnsurer,
    JobEnsurer,
    K8sResourceProbe,
    LifecycleEnsurer,
    LifecycleProbe,
    Promise,
    PromiseRegistryError,
)
from media_stack.infrastructure.promises.registry import (
    default_registry_path,
    load_registry,
)


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "promises.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# ----------------------------------------------------------------------
# Loader basics
# ----------------------------------------------------------------------


def test_default_path_points_at_contracts_promises() -> None:
    # The 4-prep commit moved the file; default_registry_path MUST
    # find the new location.
    p = default_registry_path()
    assert p.parts[-3:] == ("contracts", "promises", "promises.yaml")


def test_returns_empty_when_file_missing(tmp_path: Path) -> None:
    # Bare directory; no YAML. Loader returns [], doesn't crash —
    # operators can run with no promises at all.
    out = load_registry(tmp_path / "missing.yaml")
    assert out == []


def test_loads_real_registry_without_error() -> None:
    # The real contracts/promises/promises.yaml has ~50 entries
    # across multiple probe + ensurer types. If the loader crashes
    # on it, the schema drift needs to be fixed before Phase 4b.
    promises = load_registry()
    assert len(promises) > 30, (
        f"expected ~50 entries in real registry, got {len(promises)}"
    )
    # Each promise has a known probe + ensurer kind.
    for p in promises:
        assert p.probe.kind in {
            "lifecycle", "http_json", "http_text", "http_status",
            "file_json", "file_text", "k8s_resource", "k8s_exec",
        }, f"{p.id}: unknown probe kind {p.probe.kind!r}"
        assert p.ensurer.kind in {"lifecycle", "job", "deploy", "infra"}, (
            f"{p.id}: unknown ensurer kind {p.ensurer.kind!r}"
        )


# ----------------------------------------------------------------------
# Legacy-schema parsing
# ----------------------------------------------------------------------


def test_legacy_string_ensurer_becomes_job_ensurer(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
promises:
  - id: bazarr-language-profile
    description: ensure default profile
    ensured_by: ensure-bazarr-language-profile
    platforms: [compose, k8s]
    probe:
      type: http_json
      service: bazarr
      path: /api/system/languages/profiles
      auth: api_key
      assert: "len(response) > 0"
""")
    [promise] = load_registry(p)
    assert isinstance(promise.ensurer, JobEnsurer)
    assert promise.ensurer.job_name == "ensure-bazarr-language-profile"
    assert isinstance(promise.probe, HttpJsonProbe)
    assert promise.probe.assert_expr == "len(response) > 0"
    assert promise.probe.auth == "api_key"


def test_infra_vocabulary_string_becomes_infra_ensurer(tmp_path: Path) -> None:
    # The legacy ``kubectl-apply`` / ``operator`` /
    # ``seed-runtime-overrides`` strings are out-of-band — operator
    # owns them, controller doesn't run them.
    p = _write(tmp_path, """
version: 1
promises:
  - id: gateway-https-listener-up
    description: edge proxy serves TLS
    ensured_by: kubectl-apply
    platforms: [k8s]
    probe:
      type: k8s_resource
      kind: ingress
      namespace: media-stack
      assert: "len(resources) > 0"
""")
    [promise] = load_registry(p)
    assert isinstance(promise.ensurer, InfraEnsurer)
    assert promise.ensurer.operator == "kubectl-apply"
    assert isinstance(promise.probe, K8sResourceProbe)
    assert promise.probe.resource_kind == "ingress"


# ----------------------------------------------------------------------
# New-schema (lifecycle) parsing
# ----------------------------------------------------------------------


def test_lifecycle_typed_ensurer_parses(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
promises:
  - id: jellyfin-api-key-discoverable
    description: Jellyfin api key in env or db
    depends_on: [jellyfin-running]
    ensured_by:
      type: lifecycle
      service: jellyfin
      method: mint_api_key
    platforms: [compose, k8s]
    probe:
      type: lifecycle
      service: jellyfin
      method: probe_has_api_key
""")
    [promise] = load_registry(p)
    assert isinstance(promise.ensurer, LifecycleEnsurer)
    assert promise.ensurer.service == "jellyfin"
    assert promise.ensurer.method == "mint_api_key"
    assert isinstance(promise.probe, LifecycleProbe)
    assert promise.probe.service == "jellyfin"
    assert promise.probe.method == "probe_has_api_key"
    assert promise.depends_on == ("jellyfin-running",)


def test_deploy_typed_ensurer_parses(tmp_path: Path) -> None:
    # Sketched in the ADR — orchestrator dispatch is Phase 5+, but
    # the loader MUST accept the shape today so the schema is
    # forward-compatible.
    p = _write(tmp_path, """
version: 1
promises:
  - id: jellyfin-running
    description: Jellyfin HTTP responds
    ensured_by:
      type: deploy
      target: jellyfin
    platforms: [compose, k8s]
    probe:
      type: lifecycle
      service: jellyfin
      method: probe_running
""")
    [promise] = load_registry(p)
    assert isinstance(promise.ensurer, DeployEnsurer)
    assert promise.ensurer.target == "jellyfin"


# ----------------------------------------------------------------------
# Errors are operator-actionable
# ----------------------------------------------------------------------


def test_missing_id_raises_actionable_error(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
promises:
  - description: no id given
    ensured_by: ensure-x
    platforms: [compose]
    probe: { type: http_status, service: x, path: /, assert: "True" }
""")
    with pytest.raises(PromiseRegistryError) as exc:
        load_registry(p)
    assert "id" in str(exc.value).lower()


def test_missing_platforms_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
promises:
  - id: x
    description: no platforms
    ensured_by: ensure-x
    probe: { type: http_status, service: x, path: /, assert: "True" }
""")
    with pytest.raises(PromiseRegistryError) as exc:
        load_registry(p)
    assert "platforms" in str(exc.value)
    assert "x" in str(exc.value)  # promise id named in the error


def test_unknown_probe_type_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
promises:
  - id: x
    description: bogus probe
    ensured_by: ensure-x
    platforms: [compose]
    probe: { type: telepathy, assert: "True" }
""")
    with pytest.raises(PromiseRegistryError) as exc:
        load_registry(p)
    assert "telepathy" in str(exc.value)
    assert "x" in str(exc.value)


def test_unknown_ensurer_type_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
promises:
  - id: x
    description: bogus ensurer
    ensured_by: { type: telepathy, target: x }
    platforms: [compose]
    probe: { type: http_status, service: x, path: /, assert: "True" }
""")
    with pytest.raises(PromiseRegistryError) as exc:
        load_registry(p)
    assert "telepathy" in str(exc.value)


def test_empty_string_ensurer_raises(tmp_path: Path) -> None:
    # Distinguishes "field present but empty" from "field missing"
    # — both are operator typos, both should error clearly.
    p = _write(tmp_path, """
version: 1
promises:
  - id: x
    description: empty ensurer
    ensured_by: ""
    platforms: [compose]
    probe: { type: http_status, service: x, path: /, assert: "True" }
""")
    with pytest.raises(PromiseRegistryError) as exc:
        load_registry(p)
    assert "x" in str(exc.value)
