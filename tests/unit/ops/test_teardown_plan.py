"""Tests for the cross-platform teardown plan builder.

Targets the *plan* layer in ``bin/ops/teardown.py`` — a pure function
that translates CLI args + filesystem/tooling state into a list of
``Action`` objects. Execution is a separate layer (it shells out to
docker / kubectl / rm) and is exercised by the bash-replacement
script's own integration tests.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
TEARDOWN_PATH = REPO_ROOT / "bin" / "ops" / "teardown.py"


def _load_teardown():
    """Load the teardown script as a module — it lives outside
    ``src/`` so a regular import won't resolve."""
    spec = importlib.util.spec_from_file_location(
        "_teardown_under_test", TEARDOWN_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_teardown_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


teardown = _load_teardown()


def _args(**overrides):
    """Build an argparse-style namespace with sensible defaults."""
    base = {
        "target": "compose",
        "scope": "config",
        "compose_file": str(REPO_ROOT / "deploy" / "compose"
                            / "docker-compose.yml"),
        "config_root": str(REPO_ROOT / "config"),
        "data_root": str(REPO_ROOT / "data"),
        "media_root": str(REPO_ROOT / "media"),
        "k8s_namespace": "media-stack",
        "dry_run": True,
        "yes": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_temp_layout(tmp: Path) -> dict[str, Path]:
    config = tmp / "config"
    config.mkdir()
    (config / "defaults").mkdir()
    (config / "sonarr").mkdir()
    (config / "radarr").mkdir()
    data = tmp / "data"
    data.mkdir()
    (data / "torrents").mkdir()
    media = tmp / "media"
    media.mkdir()
    return {"config": config, "data": data, "media": media}


# ---------------------------------------------------------------------------


class TestTargetSelection:
    def test_target_compose_emits_compose_down_when_docker_present(
        self,
    ) -> None:
        with patch.object(teardown, "has_docker", return_value=True), \
             patch.object(teardown, "has_kubectl", return_value=False), \
             patch.object(teardown, "docker_compose_args",
                          return_value=["docker", "compose"]), \
             patch.object(teardown, "find_pids_listening_on",
                          return_value=[]), \
             patch.object(teardown, "list_config_subdirs_to_wipe",
                          return_value=[]):
            plan = teardown.build_plan(_args(target="compose"))
        kinds = [a.kind for a in plan.actions]
        assert "compose-down" in kinds
        assert "k8s-delete-ns" not in kinds

    def test_target_k8s_emits_namespace_delete(self) -> None:
        with patch.object(teardown, "has_docker", return_value=False), \
             patch.object(teardown, "has_kubectl", return_value=True), \
             patch.object(teardown, "find_pids_listening_on",
                          return_value=[]), \
             patch.object(teardown, "list_config_subdirs_to_wipe",
                          return_value=[]):
            plan = teardown.build_plan(_args(target="k8s"))
        kinds = [a.kind for a in plan.actions]
        assert "k8s-delete-ns" in kinds

    def test_target_both_emits_both(self) -> None:
        with patch.object(teardown, "has_docker", return_value=True), \
             patch.object(teardown, "has_kubectl", return_value=True), \
             patch.object(teardown, "docker_compose_args",
                          return_value=["docker", "compose"]), \
             patch.object(teardown, "find_pids_listening_on",
                          return_value=[]), \
             patch.object(teardown, "list_config_subdirs_to_wipe",
                          return_value=[]):
            plan = teardown.build_plan(_args(target="both"))
        kinds = [a.kind for a in plan.actions]
        assert "compose-down" in kinds
        assert "k8s-delete-ns" in kinds

    def test_target_compose_with_no_docker_emits_refuse(self) -> None:
        with patch.object(teardown, "has_docker", return_value=False), \
             patch.object(teardown, "has_kubectl", return_value=False), \
             patch.object(teardown, "find_pids_listening_on",
                          return_value=[]), \
             patch.object(teardown, "list_config_subdirs_to_wipe",
                          return_value=[]):
            plan = teardown.build_plan(_args(target="compose"))
        descriptions = [a.description for a in plan.actions]
        assert any("docker is not on PATH" in d for d in descriptions)

    def test_target_k8s_with_no_kubectl_emits_refuse(self) -> None:
        with patch.object(teardown, "has_docker", return_value=False), \
             patch.object(teardown, "has_kubectl", return_value=False), \
             patch.object(teardown, "find_pids_listening_on",
                          return_value=[]), \
             patch.object(teardown, "list_config_subdirs_to_wipe",
                          return_value=[]):
            plan = teardown.build_plan(_args(target="k8s"))
        descriptions = [a.description for a in plan.actions]
        assert any("kubectl is not on PATH" in d for d in descriptions)


class TestAutoTarget:
    def test_auto_picks_both_when_both_present(self) -> None:
        with patch.object(teardown, "has_docker", return_value=True), \
             patch.object(teardown, "has_kubectl", return_value=True):
            assert teardown._autodetect_target() == "both"

    def test_auto_picks_k8s_when_only_kubectl(self) -> None:
        with patch.object(teardown, "has_docker", return_value=False), \
             patch.object(teardown, "has_kubectl", return_value=True):
            assert teardown._autodetect_target() == "k8s"

    def test_auto_falls_back_to_compose(self) -> None:
        with patch.object(teardown, "has_docker", return_value=False), \
             patch.object(teardown, "has_kubectl", return_value=False):
            assert teardown._autodetect_target() == "compose"


class TestPathFiltering:
    def test_config_defaults_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_temp_layout(Path(tmp))
            wipe = teardown.list_config_subdirs_to_wipe(paths["config"])
            names = {p.name for p in wipe}
            assert "defaults" not in names
            assert "sonarr" in names
            assert "radarr" in names

    def test_missing_config_root_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assert (
                teardown.list_config_subdirs_to_wipe(
                    Path(tmp) / "does-not-exist",
                )
                == []
            )


class TestScopeStaging:
    def test_scope_config_does_not_touch_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_temp_layout(Path(tmp))
            with patch.object(teardown, "has_docker", return_value=True), \
                 patch.object(teardown, "has_kubectl",
                              return_value=False), \
                 patch.object(teardown, "docker_compose_args",
                              return_value=["docker", "compose"]), \
                 patch.object(teardown, "find_pids_listening_on",
                              return_value=[]):
                plan = teardown.build_plan(_args(
                    target="compose",
                    scope="config",
                    config_root=str(paths["config"]),
                    data_root=str(paths["data"]),
                    media_root=str(paths["media"]),
                ))
            rm_paths = [a.path for a in plan.actions
                        if a.kind == "rm-tree"]
            assert all(p != paths["data"] for p in rm_paths)
            assert all(p != paths["media"] for p in rm_paths)

    def test_scope_data_includes_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_temp_layout(Path(tmp))
            with patch.object(teardown, "has_docker", return_value=True), \
                 patch.object(teardown, "has_kubectl",
                              return_value=False), \
                 patch.object(teardown, "docker_compose_args",
                              return_value=["docker", "compose"]), \
                 patch.object(teardown, "find_pids_listening_on",
                              return_value=[]):
                plan = teardown.build_plan(_args(
                    target="compose",
                    scope="data",
                    config_root=str(paths["config"]),
                    data_root=str(paths["data"]),
                    media_root=str(paths["media"]),
                ))
            rm_paths = [a.path for a in plan.actions
                        if a.kind == "rm-tree"]
            assert paths["data"] in rm_paths
            assert paths["media"] not in rm_paths

    def test_scope_everything_includes_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_temp_layout(Path(tmp))
            with patch.object(teardown, "has_docker", return_value=True), \
                 patch.object(teardown, "has_kubectl",
                              return_value=False), \
                 patch.object(teardown, "docker_compose_args",
                              return_value=["docker", "compose"]), \
                 patch.object(teardown, "find_pids_listening_on",
                              return_value=[]):
                plan = teardown.build_plan(_args(
                    target="compose",
                    scope="everything",
                    config_root=str(paths["config"]),
                    data_root=str(paths["data"]),
                    media_root=str(paths["media"]),
                ))
            media_action = next(
                a for a in plan.actions
                if a.kind == "rm-tree" and a.path == paths["media"]
            )
            assert media_action.requires_double_confirm is True


class TestPortForwardKill:
    def test_kubectl_port_forward_pid_is_queued_for_kill(self) -> None:
        def _fake_listening(port: int):
            if port == 8080:
                return [(1234, "/usr/bin/kubectl port-forward "
                         "svc/sonarr 8080:8989")]
            return []

        with patch.object(teardown, "has_docker", return_value=True), \
             patch.object(teardown, "has_kubectl", return_value=False), \
             patch.object(teardown, "docker_compose_args",
                          return_value=["docker", "compose"]), \
             patch.object(teardown, "find_pids_listening_on",
                          side_effect=_fake_listening), \
             patch.object(teardown, "list_config_subdirs_to_wipe",
                          return_value=[]):
            plan = teardown.build_plan(_args(target="compose"))
        kill_actions = [a for a in plan.actions if a.kind == "kill-pid"]
        assert len(kill_actions) == 1
        assert kill_actions[0].pid == 1234

    def test_non_kubectl_pid_is_NOT_killed(self) -> None:
        def _fake_listening(port: int):
            if port == 8080:
                return [(2222, "/usr/bin/some-other-server")]
            return []

        with patch.object(teardown, "has_docker", return_value=True), \
             patch.object(teardown, "has_kubectl", return_value=False), \
             patch.object(teardown, "docker_compose_args",
                          return_value=["docker", "compose"]), \
             patch.object(teardown, "find_pids_listening_on",
                          side_effect=_fake_listening), \
             patch.object(teardown, "list_config_subdirs_to_wipe",
                          return_value=[]):
            plan = teardown.build_plan(_args(target="compose"))
        kill_actions = [a for a in plan.actions if a.kind == "kill-pid"]
        assert kill_actions == []


class TestKubectlPortForwardHeuristic:
    def test_matches_canonical_kubectl_command(self) -> None:
        cmd = "/usr/bin/kubectl port-forward svc/sonarr 8989:8989"
        assert teardown.is_kubectl_port_forward(cmd) is True

    def test_rejects_unrelated_process(self) -> None:
        assert teardown.is_kubectl_port_forward("/usr/bin/sshd") is False
        assert teardown.is_kubectl_port_forward("ssh -L 8080:host:80 …") is False


class TestHumanBytes:
    def test_zero(self) -> None:
        assert teardown.human_bytes(0) == "0.0 B"

    def test_kib(self) -> None:
        assert teardown.human_bytes(2048) == "2.0 KiB"

    def test_mib(self) -> None:
        assert teardown.human_bytes(5 * 1024 * 1024) == "5.0 MiB"

    def test_clamped_at_tib(self) -> None:
        result = teardown.human_bytes(10 * 1024 ** 5)
        assert result.endswith("TiB")
