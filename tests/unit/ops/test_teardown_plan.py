"""Tests for the workflow-backed teardown plan builder."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from media_stack.cli.workflows.teardown_models import TeardownAction, TeardownRequest
from media_stack.cli.workflows.teardown_compose_strategy import TeardownComposeStrategy
from media_stack.cli.workflows.teardown_plan_service import TeardownPlanService
from media_stack.cli.workflows.teardown_safety_policy_service import TeardownSafetyPolicyService

REPO_ROOT = Path(__file__).resolve().parents[3]


class FakeCommandRunner:
    """No-op runner for plan tests."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run_text(self, command, *, env=None, check=True):
        self.commands.append(tuple(command))
        return ""

    def run_json(self, command, *, env=None, check=True):
        return {}


class FakeComposeStrategy:
    """Controllable Compose strategy for target-selection tests."""

    def __init__(self, *, has_docker: bool = True, action: TeardownAction | None = None) -> None:
        self._has_docker = has_docker
        self.action = action or TeardownAction(
            kind="compose-down",
            description="Stop and remove every compose container (docker-compose.yml)",
            command=("docker", "compose", "down"),
        )

    def has_docker(self) -> bool:
        return self._has_docker

    def plan(self, request: TeardownRequest):
        if not self._has_docker:
            return (TeardownAction(kind="refuse", description="docker is not on PATH — skipping compose teardown"),)
        return (self.action,)


class FakeKubernetesStrategy:
    """Controllable Kubernetes strategy for target-selection tests."""

    def __init__(self, *, has_kubectl: bool = False, action: TeardownAction | None = None) -> None:
        self._has_kubectl = has_kubectl
        self.action = action or TeardownAction(
            kind="k8s-delete-ns",
            description="Delete kubernetes namespace 'media-stack' (and every resource in it)",
            command=("kubectl", "delete", "namespace", "media-stack"),
        )

    def has_kubectl(self) -> bool:
        return self._has_kubectl

    def plan(self, request: TeardownRequest):
        if not self._has_kubectl:
            return (TeardownAction(kind="refuse", description="kubectl is not on PATH — skipping k8s teardown"),)
        return (self.action,)


def _request(**overrides) -> TeardownRequest:
    base = {
        "target": "compose",
        "scope": "config",
        "compose_file": REPO_ROOT / "deploy" / "compose" / "docker-compose.yml",
        "config_root": REPO_ROOT / "config",
        "data_root": REPO_ROOT / "data",
        "media_root": REPO_ROOT / "media",
        "k8s_namespace": "media-stack",
        "dry_run": True,
        "assume_yes": False,
        "environment": "local",
    }
    base.update(overrides)
    return TeardownRequest(**base)


def _service(*, compose=None, k8s=None) -> TeardownPlanService:
    return TeardownPlanService(
        FakeCommandRunner(),
        compose_strategy=compose or FakeComposeStrategy(),
        kubernetes_strategy=k8s or FakeKubernetesStrategy(),
    )


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


class TestTargetSelection:
    def test_target_compose_emits_compose_down_when_docker_present(self) -> None:
        svc = _service(compose=FakeComposeStrategy(has_docker=True), k8s=FakeKubernetesStrategy(has_kubectl=False))
        with patch.object(svc, "stale_port_forward_actions", return_value=[]), \
             patch.object(svc.safety_policy, "config_children_to_wipe", return_value=[]):
            plan = svc.build_plan(_request(target="compose"))
        kinds = [a.kind for a in plan.actions]
        assert "compose-down" in kinds
        assert "k8s-delete-ns" not in kinds

    def test_target_k8s_emits_namespace_delete(self) -> None:
        svc = _service(compose=FakeComposeStrategy(has_docker=False), k8s=FakeKubernetesStrategy(has_kubectl=True))
        with patch.object(svc, "stale_port_forward_actions", return_value=[]), \
             patch.object(svc.safety_policy, "config_children_to_wipe", return_value=[]):
            plan = svc.build_plan(_request(target="k8s"))
        kinds = [a.kind for a in plan.actions]
        assert "k8s-delete-ns" in kinds

    def test_target_both_emits_both(self) -> None:
        svc = _service(compose=FakeComposeStrategy(has_docker=True), k8s=FakeKubernetesStrategy(has_kubectl=True))
        with patch.object(svc, "stale_port_forward_actions", return_value=[]), \
             patch.object(svc.safety_policy, "config_children_to_wipe", return_value=[]):
            plan = svc.build_plan(_request(target="both"))
        kinds = [a.kind for a in plan.actions]
        assert "compose-down" in kinds
        assert "k8s-delete-ns" in kinds

    def test_target_compose_with_no_docker_emits_refuse(self) -> None:
        svc = _service(compose=FakeComposeStrategy(has_docker=False), k8s=FakeKubernetesStrategy(has_kubectl=False))
        with patch.object(svc, "stale_port_forward_actions", return_value=[]), \
             patch.object(svc.safety_policy, "config_children_to_wipe", return_value=[]):
            plan = svc.build_plan(_request(target="compose"))
        descriptions = [a.description for a in plan.actions]
        assert any("docker is not on PATH" in d for d in descriptions)

    def test_target_k8s_with_no_kubectl_emits_refuse(self) -> None:
        svc = _service(compose=FakeComposeStrategy(has_docker=False), k8s=FakeKubernetesStrategy(has_kubectl=False))
        with patch.object(svc, "stale_port_forward_actions", return_value=[]), \
             patch.object(svc.safety_policy, "config_children_to_wipe", return_value=[]):
            plan = svc.build_plan(_request(target="k8s"))
        descriptions = [a.description for a in plan.actions]
        assert any("kubectl is not on PATH" in d for d in descriptions)


class TestAutoTarget:
    def test_auto_picks_both_when_both_present(self) -> None:
        assert _service(compose=FakeComposeStrategy(has_docker=True), k8s=FakeKubernetesStrategy(has_kubectl=True)).resolve_target("auto") == "both"

    def test_auto_picks_k8s_when_only_kubectl(self) -> None:
        assert _service(compose=FakeComposeStrategy(has_docker=False), k8s=FakeKubernetesStrategy(has_kubectl=True)).resolve_target("auto") == "k8s"

    def test_auto_falls_back_to_compose(self) -> None:
        assert _service(compose=FakeComposeStrategy(has_docker=False), k8s=FakeKubernetesStrategy(has_kubectl=False)).resolve_target("auto") == "compose"


class TestPathFiltering:
    def test_config_defaults_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_temp_layout(Path(tmp))
            wipe = TeardownSafetyPolicyService().config_children_to_wipe(paths["config"])
            names = {p.name for p in wipe}
            assert "defaults" not in names
            assert "sonarr" in names
            assert "radarr" in names

    def test_missing_config_root_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assert TeardownSafetyPolicyService().config_children_to_wipe(Path(tmp) / "does-not-exist") == []

    def test_protected_namespace_is_refused(self) -> None:
        action = TeardownSafetyPolicyService().namespace_delete_action(_request(k8s_namespace="kube-system"))
        assert action.kind == "refuse"
        assert "protected" in action.description

    def test_media_stack_namespace_is_allowed(self) -> None:
        action = TeardownSafetyPolicyService().namespace_delete_action(_request(k8s_namespace="media-stack"))
        assert action.kind == "k8s-delete-ns"
        assert action.command == (
            "kubectl",
            "delete",
            "namespace",
            "media-stack",
            "--ignore-not-found=true",
            "--wait=true",
        )

    def test_protected_path_denial_replaces_delete_action(self) -> None:
        request = _request()
        plan = _service().empty_plan(
            request,
            TeardownAction(
                kind="rm-tree",
                description="Delete defaults",
                path=request.config_root / "defaults",
            ),
        )
        validated = TeardownSafetyPolicyService().validate_plan(plan)
        assert validated.actions[0].kind == "refuse"
        assert "protected path" in validated.actions[0].description

    def test_prod_execute_requires_explicit_confirmation_token(self) -> None:
        svc = _service()
        plan = svc.build_plan(_request(environment="prod", dry_run=False, target="k8s"))
        assert [action.kind for action in plan.actions] == ["refuse"]
        assert "production teardown" in plan.actions[0].description

    def test_prod_dry_run_can_preview_without_confirmation_token(self) -> None:
        svc = _service(k8s=FakeKubernetesStrategy(has_kubectl=True))
        with patch.object(svc, "stale_port_forward_actions", return_value=[]), \
             patch.object(svc.safety_policy, "config_children_to_wipe", return_value=[]):
            plan = svc.build_plan(_request(environment="prod", dry_run=True, target="k8s"))
        assert any(action.kind == "k8s-delete-ns" for action in plan.actions)


class TestComposeCommandGeneration:
    def test_compose_strategy_generates_exact_down_command(self) -> None:
        runner = FakeCommandRunner()
        strategy = TeardownComposeStrategy(runner)
        with patch("media_stack.cli.workflows.teardown_compose_strategy.shutil.which", return_value="/usr/bin/docker"):
            actions = strategy.plan(_request(compose_file=Path("/stack/docker-compose.yml")))
        assert actions[0].kind == "compose-down"
        assert actions[0].command == (
            "docker",
            "compose",
            "-f",
            "/stack/docker-compose.yml",
            "down",
            "--remove-orphans",
        )


class TestScopeStaging:
    def test_scope_config_does_not_touch_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_temp_layout(Path(tmp))
            svc = _service()
            with patch.object(svc, "stale_port_forward_actions", return_value=[]):
                plan = svc.build_plan(_request(config_root=paths["config"], data_root=paths["data"], media_root=paths["media"]))
            rm_paths = [a.path for a in plan.actions if a.kind == "rm-tree"]
            assert all(p != paths["data"] for p in rm_paths)
            assert all(p != paths["media"] for p in rm_paths)

    def test_scope_data_includes_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_temp_layout(Path(tmp))
            svc = _service()
            with patch.object(svc, "stale_port_forward_actions", return_value=[]):
                plan = svc.build_plan(_request(scope="data", config_root=paths["config"], data_root=paths["data"], media_root=paths["media"]))
            rm_paths = [a.path for a in plan.actions if a.kind == "rm-tree"]
            assert paths["data"] in rm_paths
            assert paths["media"] not in rm_paths

    def test_scope_everything_includes_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_temp_layout(Path(tmp))
            svc = _service()
            with patch.object(svc, "stale_port_forward_actions", return_value=[]):
                plan = svc.build_plan(_request(scope="everything", config_root=paths["config"], data_root=paths["data"], media_root=paths["media"]))
            media_action = next(a for a in plan.actions if a.kind == "rm-tree" and a.path == paths["media"])
            assert media_action.requires_double_confirm is True


class TestPortForwardKill:
    def test_kubectl_port_forward_pid_is_queued_for_kill(self) -> None:
        svc = _service()
        with patch.object(svc, "find_pids_listening_on", side_effect=lambda port: [(1234, "/usr/bin/kubectl port-forward svc/sonarr 8080:8989")] if port == 8080 else []), \
             patch.object(svc.safety_policy, "config_children_to_wipe", return_value=[]):
            plan = svc.build_plan(_request(target="compose"))
        kill_actions = [a for a in plan.actions if a.kind == "kill-pid"]
        assert len(kill_actions) == 1
        assert kill_actions[0].pid == 1234

    def test_non_kubectl_pid_is_not_killed(self) -> None:
        svc = _service()
        with patch.object(svc, "find_pids_listening_on", side_effect=lambda port: [(2222, "/usr/bin/some-other-server")] if port == 8080 else []), \
             patch.object(svc.safety_policy, "config_children_to_wipe", return_value=[]):
            plan = svc.build_plan(_request(target="compose"))
        kill_actions = [a for a in plan.actions if a.kind == "kill-pid"]
        assert kill_actions == []


class TestKubectlPortForwardHeuristic:
    def test_matches_canonical_kubectl_command(self) -> None:
        assert _service().is_kubectl_port_forward("/usr/bin/kubectl port-forward svc/sonarr 8989:8989") is True

    def test_rejects_unrelated_process(self) -> None:
        svc = _service()
        assert svc.is_kubectl_port_forward("/usr/bin/sshd") is False
        assert svc.is_kubectl_port_forward("ssh -L 8080:host:80 ...") is False


class TestHumanBytes:
    def test_zero(self) -> None:
        assert _service().human_bytes(0) == "0.0 B"

    def test_kib(self) -> None:
        assert _service().human_bytes(2048) == "2.0 KiB"

    def test_mib(self) -> None:
        assert _service().human_bytes(5 * 1024 * 1024) == "5.0 MiB"

    def test_clamped_at_tib(self) -> None:
        assert _service().human_bytes(10 * 1024 ** 5).endswith("TiB")
