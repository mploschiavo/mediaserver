"""Unit tests for the Kubernetes client adapter (kube_client.py).

Covers helper functions, KubernetesClient CRUD operations, manifest
commands, error handling, and edge cases.  All external dependencies
(kubernetes SDK, subprocess, file I/O) are mocked.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.exceptions import ConfigError, KubernetesError
from media_stack.core.platforms.kubernetes.kube_client import (
    KubernetesClient,
    _env_truthy,
    _extract_path_value,
    _format_api_error,
    _is_retryable_kubectl_error,
    _parse_jsonpath_key,
    _parse_timeout_seconds,
    _render_custom_columns,
    _selector_from_match_labels,
    resolve_kubectl_binary,
)
from media_stack.core.subprocess_utils import CommandRunner


# ---------------------------------------------------------------------------
# Helpers to build a pre-wired KubernetesClient with mocked K8s SDK objects
# ---------------------------------------------------------------------------

def _make_client(**overrides: object) -> KubernetesClient:
    """Return a KubernetesClient whose internal K8s SDK attrs are mocks."""
    client = KubernetesClient(cmd_prefix=["kubectl"], runner=CommandRunner())
    client._k8s_client = overrides.get("k8s_client", MagicMock())
    client._k8s_config = MagicMock()
    client._k8s_dynamic = MagicMock()
    client._k8s_stream = overrides.get("k8s_stream", MagicMock())
    client._api_client = MagicMock()
    client._core_v1 = overrides.get("core_v1", MagicMock())
    client._apps_v1 = overrides.get("apps_v1", MagicMock())
    client._batch_v1 = overrides.get("batch_v1", MagicMock())
    client._networking_v1 = overrides.get("networking_v1", MagicMock())
    client._dynamic_client = overrides.get("dynamic_client", MagicMock())
    return client


def _mock_obj(data: dict) -> MagicMock:
    """Return a mock whose .to_dict() returns *data*."""
    m = MagicMock()
    m.to_dict.return_value = data
    return m


# ---------------------------------------------------------------------------
# 1-6: Pure helper function tests
# ---------------------------------------------------------------------------

class TestHelperFunctions(unittest.TestCase):
    """Tests for module-level helper functions."""

    # 1
    @patch.dict("os.environ", {"MY_FLAG": "true"})
    def test_env_truthy_returns_true_for_true_string(self):
        self.assertTrue(_env_truthy("MY_FLAG", default=False))

    # 2
    @patch.dict("os.environ", {"MY_FLAG": "no"})
    def test_env_truthy_returns_false_for_no_string(self):
        self.assertFalse(_env_truthy("MY_FLAG", default=True))

    # 3
    def test_env_truthy_missing_var_returns_default(self):
        import os
        os.environ.pop("_ABSENT_VAR_", None)
        self.assertTrue(_env_truthy("_ABSENT_VAR_", default=True))

    # 4
    def test_is_retryable_connection_refused(self):
        self.assertTrue(_is_retryable_kubectl_error(Exception("connection refused")))

    # 5
    def test_is_retryable_false_for_generic_error(self):
        self.assertFalse(_is_retryable_kubectl_error(Exception("bad request")))

    # 6
    def test_format_api_error_extracts_status_and_body(self):
        exc = Exception("boom")
        exc.status = 404
        exc.body = "not found"
        exc.reason = "Not Found"
        status, msg = _format_api_error(exc)
        self.assertEqual(status, 404)
        self.assertEqual(msg, "not found")


# ---------------------------------------------------------------------------
# 7-10: Selector, timeout, jsonpath, extract-path helpers
# ---------------------------------------------------------------------------

class TestParsingHelpers(unittest.TestCase):

    # 7
    def test_selector_from_match_labels(self):
        self.assertEqual(
            _selector_from_match_labels({"app": "sonarr", "tier": "media"}),
            "app=sonarr,tier=media",
        )

    # 8
    def test_parse_timeout_seconds_with_suffix(self):
        self.assertEqual(_parse_timeout_seconds("30s"), 30)
        self.assertEqual(_parse_timeout_seconds("", 90), 90)
        self.assertEqual(_parse_timeout_seconds("abc", 45), 45)

    # 9
    def test_parse_jsonpath_key_standard(self):
        self.assertEqual(_parse_jsonpath_key("jsonpath={.data.password}"), "password")
        self.assertEqual(_parse_jsonpath_key("jsonpath={.spec.replicas}"), "")

    # 10
    def test_extract_path_value_nested_and_indexed(self):
        self.assertEqual(
            _extract_path_value({"metadata": {"name": "foo"}}, "metadata.name"), "foo"
        )
        self.assertEqual(_extract_path_value({}, "a.b.c"), "")
        payload = {"items": [{"name": "a"}, {"name": "b"}]}
        self.assertEqual(_extract_path_value(payload, "items[1].name"), "b")


# ---------------------------------------------------------------------------
# 11-12: Custom columns rendering
# ---------------------------------------------------------------------------

class TestRenderCustomColumns(unittest.TestCase):

    # 11
    def test_render_with_headers(self):
        rows = [{"metadata": {"name": "pod-1"}, "status": {"phase": "Running"}}]
        result = _render_custom_columns(
            rows, "NAME:.metadata.name,PHASE:.status.phase", no_headers=False
        )
        self.assertIn("NAME", result)
        self.assertIn("pod-1", result)

    # 12
    def test_render_no_headers(self):
        rows = [{"metadata": {"name": "pod-1"}}]
        result = _render_custom_columns(rows, "NAME:.metadata.name", no_headers=True)
        self.assertNotIn("NAME", result)
        self.assertIn("pod-1", result)


# ---------------------------------------------------------------------------
# 13-15: resolve_kubectl_binary
# ---------------------------------------------------------------------------

class TestResolveKubectlBinary(unittest.TestCase):

    # 13
    @patch("shutil.which", side_effect=lambda x: "/snap/bin/microk8s" if x == "microk8s" else None)
    def test_microk8s_preferred(self, _w):
        self.assertEqual(resolve_kubectl_binary(), ["microk8s", "kubectl"])

    # 14
    @patch("shutil.which", side_effect=lambda x: "/usr/bin/kubectl" if x == "kubectl" else None)
    def test_kubectl_fallback(self, _w):
        self.assertEqual(resolve_kubectl_binary(), ["kubectl"])

    # 15
    @patch("shutil.which", return_value=None)
    def test_no_binary_raises(self, _w):
        with self.assertRaises(ConfigError):
            resolve_kubectl_binary()


# ---------------------------------------------------------------------------
# 16-18: Top-level run() dispatcher
# ---------------------------------------------------------------------------

class TestClientRunDispatcher(unittest.TestCase):

    # 16
    def test_unsupported_command_returns_error(self):
        result = _make_client().run(["unsupported_cmd"], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported", result.stderr)

    # 17
    def test_empty_args_returns_error(self):
        result = _make_client().run([], check=False)
        self.assertNotEqual(result.returncode, 0)

    # 18
    def test_check_true_raises_kubernetes_error(self):
        with self.assertRaises(KubernetesError):
            _make_client().run(["unsupported_cmd"], check=True)


# ---------------------------------------------------------------------------
# 19-25: GET operations
# ---------------------------------------------------------------------------

class TestRunGet(unittest.TestCase):

    # 19
    def test_get_pods_json_output(self):
        core = MagicMock()
        pod_data = {"metadata": {"name": "my-pod"}, "status": {"phase": "Running"}}
        list_result = MagicMock()
        list_result.items = [_mock_obj(pod_data)]
        core.list_namespaced_pod.return_value = list_result

        result = _make_client(core_v1=core).run(
            ["-n", "media", "get", "pods", "-o", "json"], check=False
        )
        self.assertEqual(result.returncode, 0)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed[0]["metadata"]["name"], "my-pod")

    # 20
    def test_get_single_deployment(self):
        apps = MagicMock()
        apps.read_namespaced_deployment.return_value = _mock_obj({"metadata": {"name": "sonarr"}})

        result = _make_client(apps_v1=apps).run(["get", "deployment", "sonarr"], check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("sonarr", result.stdout)

    # 21
    def test_get_secret_jsonpath(self):
        core = MagicMock()
        core.read_namespaced_secret.return_value = _mock_obj(
            {"data": {"password": "c2VjcmV0"}, "metadata": {"name": "s1"}}
        )
        result = _make_client(core_v1=core).run(
            ["get", "secret", "s1", "-o", "jsonpath={.data.password}"], check=False
        )
        self.assertEqual(result.stdout, "c2VjcmV0")

    # 22
    def test_get_namespace_list(self):
        core = MagicMock()
        list_result = MagicMock()
        list_result.items = [_mock_obj({"metadata": {"name": "media"}})]
        core.list_namespace.return_value = list_result

        result = _make_client(core_v1=core).run(["get", "namespaces"], check=False)
        self.assertIn("media", result.stdout)

    # 23
    def test_get_unsupported_resource_type(self):
        result = _make_client().run(["get", "cronjobs"], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported get resource type", result.stderr)

    # 24
    def test_get_with_slash_syntax(self):
        core = MagicMock()
        core.read_namespaced_secret.return_value = _mock_obj(
            {"metadata": {"name": "my-secret"}, "data": {}}
        )
        result = _make_client(core_v1=core).run(
            ["get", "secret/my-secret", "-o", "json"], check=False
        )
        self.assertEqual(result.returncode, 0)

    # 25
    def test_get_wide_output(self):
        core = MagicMock()
        pod_data = {
            "metadata": {"name": "p1"},
            "status": {"phase": "Running", "pod_ip": "10.0.0.1"},
            "spec": {"node_name": "node-1"},
        }
        list_result = MagicMock()
        list_result.items = [_mock_obj(pod_data)]
        core.list_namespaced_pod.return_value = list_result

        result = _make_client(core_v1=core).run(["get", "pods", "-o", "wide"], check=False)
        self.assertIn("10.0.0.1", result.stdout)


# ---------------------------------------------------------------------------
# 26-27: DESCRIBE operations
# ---------------------------------------------------------------------------

class TestRunDescribe(unittest.TestCase):

    # 26
    def test_describe_pod(self):
        core = MagicMock()
        core.read_namespaced_pod.return_value = _mock_obj(
            {"metadata": {"name": "p1"}, "spec": {}}
        )
        result = _make_client(core_v1=core).run(["describe", "pod", "p1"], check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("p1", result.stdout)

    # 27
    def test_describe_insufficient_args(self):
        result = _make_client().run(["describe", "pod"], check=False)
        self.assertNotEqual(result.returncode, 0)


# ---------------------------------------------------------------------------
# 28-29: LOGS operations
# ---------------------------------------------------------------------------

class TestRunLogs(unittest.TestCase):

    # 28
    def test_logs_direct_pod(self):
        core = MagicMock()
        core.read_namespaced_pod_log.return_value = "line1\nline2"

        result = _make_client(core_v1=core).run(["logs", "my-pod"], check=False)
        self.assertIn("line1", result.stdout)

    # 29
    def test_logs_job_target_resolves_pod(self):
        core = MagicMock()
        pod = MagicMock()
        pod.status = SimpleNamespace(phase="Running")
        pod.metadata = SimpleNamespace(name="job-pod-abc")
        list_result = MagicMock()
        list_result.items = [pod]
        core.list_namespaced_pod.return_value = list_result
        core.read_namespaced_pod_log.return_value = "job output"

        result = _make_client(core_v1=core).run(["logs", "job/my-job"], check=False)
        self.assertIn("job output", result.stdout)


# ---------------------------------------------------------------------------
# 30-32: ROLLOUT operations
# ---------------------------------------------------------------------------

class TestRunRollout(unittest.TestCase):

    # 30
    def test_rollout_restart(self):
        apps = MagicMock()
        result = _make_client(apps_v1=apps).run(
            ["rollout", "restart", "deployment/sonarr"], check=False
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("restarted", result.stdout)
        apps.patch_namespaced_deployment.assert_called_once()

    # 31
    @patch("time.sleep")
    @patch("time.time")
    def test_rollout_status_success(self, mock_time, _sleep):
        mock_time.side_effect = [0, 0, 1]
        apps = MagicMock()
        dep = MagicMock()
        dep.spec = SimpleNamespace(replicas=1)
        dep.metadata = SimpleNamespace(generation=1)
        dep.status = SimpleNamespace(
            updated_replicas=1, ready_replicas=1,
            available_replicas=1, observed_generation=1,
        )
        apps.read_namespaced_deployment.return_value = dep

        result = _make_client(apps_v1=apps).run(
            ["rollout", "status", "deployment/sonarr", "--timeout=60s"], check=False
        )
        self.assertIn("successfully rolled out", result.stdout)

    # 32
    def test_rollout_unsupported_subcommand(self):
        result = _make_client().run(["rollout", "undo", "deployment/sonarr"], check=False)
        self.assertNotEqual(result.returncode, 0)


# ---------------------------------------------------------------------------
# 33-34: PATCH operations
# ---------------------------------------------------------------------------

class TestRunPatch(unittest.TestCase):

    # 33
    def test_patch_secret(self):
        core = MagicMock()
        result = _make_client(core_v1=core).run(
            ["patch", "secret", "my-secret", "-p", '{"data":{"key":"dmFs"}}'],
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("patched", result.stdout)

    # 34
    def test_patch_invalid_json_returns_error(self):
        result = _make_client().run(
            ["patch", "secret", "s", "-p", "not-json"], check=False
        )
        self.assertNotEqual(result.returncode, 0)


# ---------------------------------------------------------------------------
# 35-36: DELETE operations
# ---------------------------------------------------------------------------

class TestRunDelete(unittest.TestCase):

    # 35
    def test_delete_job(self):
        batch = MagicMock()
        result = _make_client(batch_v1=batch).run(["delete", "job", "my-job"], check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("deleted", result.stdout)

    # 36
    def test_delete_ignore_not_found_suppresses_404(self):
        batch = MagicMock()
        exc = Exception("not found")
        exc.status = 404
        exc.body = "not found"
        exc.reason = ""
        batch.delete_namespaced_job.side_effect = exc

        result = _make_client(batch_v1=batch).run(
            ["delete", "job", "gone", "--ignore-not-found"], check=False
        )
        self.assertEqual(result.returncode, 0)


# ---------------------------------------------------------------------------
# 37: SCALE operation
# ---------------------------------------------------------------------------

class TestRunScale(unittest.TestCase):

    # 37
    def test_scale_deployment(self):
        apps = MagicMock()
        result = _make_client(apps_v1=apps).run(
            ["scale", "deployment/sonarr", "--replicas=2"], check=False
        )
        self.assertIn("scaled", result.stdout)


# ---------------------------------------------------------------------------
# 38: EXEC operation
# ---------------------------------------------------------------------------

class TestRunExec(unittest.TestCase):

    # 38
    def test_exec_direct_pod(self):
        stream_mod = MagicMock()
        ws = MagicMock()
        ws.is_open.side_effect = [True, False]
        ws.peek_stdout.return_value = True
        ws.read_stdout.return_value = "hello"
        ws.peek_stderr.return_value = False
        ws.returncode = 0
        stream_mod.stream.return_value = ws

        result = _make_client(k8s_stream=stream_mod).run(
            ["exec", "my-pod", "--", "echo", "hello"], check=False
        )
        self.assertIn("hello", result.stdout)


# ---------------------------------------------------------------------------
# 39-40: Manifest / namespace creation, and from_environment
# ---------------------------------------------------------------------------

class TestManifestAndFactory(unittest.TestCase):

    # 39
    def test_apply_from_stdin(self):
        dyn = MagicMock()
        resource = MagicMock()
        resource.namespaced = True
        dyn.resources.get.return_value = resource

        manifest = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: test-cm\ndata: {}\n"
        result = _make_client(dynamic_client=dyn).run(
            ["apply", "-f", "-"], check=False, input_text=manifest
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("configured", result.stdout)

    # 40
    @patch.dict("os.environ", {"KUBECTL_CMD": "custom-kubectl --kubeconfig /etc/k.conf"})
    def test_from_environment_override(self):
        client = KubernetesClient.from_environment()
        self.assertEqual(
            client.cmd_prefix,
            ["custom-kubectl", "--kubeconfig", "/etc/k.conf"],
        )


if __name__ == "__main__":
    unittest.main()
