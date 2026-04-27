"""Tests for the k8s config export tool.

Targets the pure helpers (sanitize, apply-priority, restore-md
renderer, kubectl-shell shape). The end-to-end ``write_export``
path is exercised against a fake-resource list to confirm it
produces the documented filesystem layout.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPORT_PATH = REPO_ROOT / "bin" / "ops" / "k8s_export.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_k8s_export_under_test", EXPORT_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_k8s_export_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


k8s = _load_module()


def _ingress(name: str = "controller") -> dict:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": name,
            "namespace": "media-stack",
            "creationTimestamp": "2026-01-01T00:00:00Z",
            "resourceVersion": "12345",
            "uid": "abc-123",
            "managedFields": [{"manager": "kubectl"}],
            "annotations": {
                "operator/comment": "keep me",
                "kubectl.kubernetes.io/last-applied-configuration":
                    "{...big blob...}",
            },
        },
        "spec": {"rules": []},
        "status": {"loadBalancer": {}},
    }


class TestSanitizeResource:
    def test_strips_status(self) -> None:
        out = k8s.sanitize_resource(_ingress())
        assert "status" not in out

    def test_strips_uid_and_resource_version(self) -> None:
        out = k8s.sanitize_resource(_ingress())
        meta = out["metadata"]
        for forbidden in ("uid", "resourceVersion", "creationTimestamp",
                          "managedFields"):
            assert forbidden not in meta

    def test_strips_last_applied_configuration_annotation(self) -> None:
        out = k8s.sanitize_resource(_ingress())
        anns = out["metadata"].get("annotations", {})
        assert "kubectl.kubernetes.io/last-applied-configuration" not in anns
        # User-set annotation survives.
        assert anns.get("operator/comment") == "keep me"

    def test_drops_annotations_when_empty_after_strip(self) -> None:
        ing = _ingress()
        # Only the strip-listed annotation present.
        ing["metadata"]["annotations"] = {
            "kubectl.kubernetes.io/last-applied-configuration": "x",
        }
        out = k8s.sanitize_resource(ing)
        assert "annotations" not in out["metadata"]

    def test_preserves_spec(self) -> None:
        ing = _ingress()
        ing["spec"]["rules"] = [
            {"host": "media-stack.local",
             "http": {"paths": [{"path": "/", "backend": {}}]}},
        ]
        out = k8s.sanitize_resource(ing)
        assert out["spec"]["rules"][0]["host"] == "media-stack.local"


class TestApplyPriority:
    def test_namespace_first(self) -> None:
        items = [
            {"kind": "Ingress"},
            {"kind": "Namespace"},
            {"kind": "Secret"},
            {"kind": "ConfigMap"},
        ]
        items.sort(key=k8s.apply_priority)
        assert [i["kind"] for i in items] == [
            "Namespace", "Secret", "ConfigMap", "Ingress",
        ]

    def test_unknown_kind_sorts_last(self) -> None:
        items = [
            {"kind": "Ingress"},
            {"kind": "WeirdCRD"},
        ]
        items.sort(key=k8s.apply_priority)
        assert items[0]["kind"] == "Ingress"
        assert items[-1]["kind"] == "WeirdCRD"


class TestKubectlGetJson:
    def test_returns_empty_items_on_kubectl_failure(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert k8s.kubectl_get_json(
                "ingress", "media-stack",
            ) == {"items": []}

    def test_parses_kubectl_output(self) -> None:
        from unittest.mock import MagicMock
        proc = MagicMock()
        proc.stdout = json.dumps({"items": [{"kind": "Ingress"}]})
        with patch("subprocess.run", return_value=proc):
            out = k8s.kubectl_get_json("ingress", "media-stack")
            assert out["items"][0]["kind"] == "Ingress"

    def test_label_selector_threaded_into_command(self) -> None:
        from unittest.mock import MagicMock
        proc = MagicMock()
        proc.stdout = '{"items": []}'
        with patch("subprocess.run", return_value=proc) as run:
            k8s.kubectl_get_json(
                "configmap", "ms",
                label_selector="operator-owned=true",
            )
            call_args = run.call_args[0][0]
            assert "-l" in call_args
            assert "operator-owned=true" in call_args


class TestWriteExport:
    def test_writes_each_resource_under_per_kind_folder(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            import pytest
            pytest.skip("pyyaml not installed")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "export"
            resources = [
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": "media-stack"},
                },
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "type": "kubernetes.io/tls",
                    "metadata": {"name": "wildcard-tls"},
                },
                _ingress("controller"),
            ]
            cleaned = [k8s.sanitize_resource(r) for r in resources]
            k8s.write_export(out, cleaned, "media-stack")
            assert (out / "namespace" / "media-stack.yaml").is_file()
            assert (out / "secret" / "wildcard-tls.yaml").is_file()
            assert (out / "ingress" / "controller.yaml").is_file()
            assert (out / "manifest.yaml").is_file()
            assert (out / "RESTORE.md").is_file()

    def test_manifest_contains_all_resources(self) -> None:
        try:
            import yaml
        except ImportError:
            import pytest
            pytest.skip("pyyaml not installed")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "export"
            resources = [
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": "media-stack"},
                },
                _ingress("controller"),
            ]
            cleaned = [k8s.sanitize_resource(r) for r in resources]
            k8s.write_export(out, cleaned, "media-stack")
            docs = list(yaml.safe_load_all(
                (out / "manifest.yaml").read_text(encoding="utf-8"),
            ))
            kinds = {d["kind"] for d in docs}
            assert kinds == {"Namespace", "Ingress"}


class TestRestoreMd:
    def test_includes_namespace_and_counts(self) -> None:
        out = k8s._render_restore_md(
            "media-stack", {"Secret": 2, "Ingress": 1},
        )
        assert "media-stack" in out
        assert "**Secret**: 2" in out
        assert "**Ingress**: 1" in out
        assert "kubectl apply -f manifest.yaml" in out

    def test_warns_about_missing_pvc_data(self) -> None:
        out = k8s._render_restore_md("media-stack", {})
        assert "PVC" in out
        assert "Deployments" in out
