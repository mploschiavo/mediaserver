"""Safety rules for destructive teardown workflows."""

from __future__ import annotations

from pathlib import Path

from media_stack.cli.workflows.teardown_models import (
    PROD_CONFIRMATION_PREFIX,
    TEARDOWN_ACTION_REFUSE,
    TEARDOWN_ACTION_RM_TREE,
    TEARDOWN_ENV_PROD,
    TeardownAction,
    TeardownPlan,
    TeardownRequest,
)


class TeardownSafetyPolicyService:
    """Centralized allow/deny rules for teardown planning."""

    protected_config_subdirs = frozenset({"defaults"})
    protected_namespaces = frozenset({"default", "kube-system", "kube-public", "kube-node-lease"})

    def request_denial_action(self, request: TeardownRequest) -> TeardownAction | None:
        if request.environment == TEARDOWN_ENV_PROD and not request.dry_run:
            expected = self.production_confirmation_token(request)
            if request.confirmation_token.strip() != expected:
                return TeardownAction(
                    kind=TEARDOWN_ACTION_REFUSE,
                    description=(
                        "refusing production teardown without confirmation token "
                        f"'{expected}'"
                    ),
                )
        return None

    def production_confirmation_token(self, request: TeardownRequest) -> str:
        namespace = request.k8s_namespace.strip() or "media-stack"
        return f"{PROD_CONFIRMATION_PREFIX} {namespace}"

    def config_children_to_wipe(self, config_root: Path) -> list[Path]:
        if not config_root.is_dir():
            return []
        out: list[Path] = []
        for child in sorted(config_root.iterdir()):
            if not child.is_dir() and not child.is_file():
                continue
            if child.name in self.protected_config_subdirs:
                continue
            out.append(child)
        return out

    def namespace_delete_action(self, request: TeardownRequest) -> TeardownAction:
        namespace = request.k8s_namespace.strip()
        if namespace in self.protected_namespaces or not namespace:
            return TeardownAction(
                kind="refuse",
                description=f"refusing to delete protected kubernetes namespace '{namespace}'",
            )
        return TeardownAction(
            kind="k8s-delete-ns",
            description=f"Delete kubernetes namespace '{namespace}' (and every resource in it)",
            command=("kubectl", "delete", "namespace", namespace, "--ignore-not-found=true", "--wait=true"),
            confirm_text=f"Delete the entire '{namespace}' namespace?",
        )

    def validate_plan(self, plan: TeardownPlan) -> TeardownPlan:
        actions = []
        for action in plan.actions:
            if action.kind == TEARDOWN_ACTION_RM_TREE and self.path_is_protected(action.path, plan):
                actions.append(
                    TeardownAction(
                        kind="refuse",
                        description=f"refusing to delete protected path {action.path}",
                    )
                )
            else:
                actions.append(action)
        return TeardownPlan(
            target=plan.target,
            scope=plan.scope,
            compose_file=plan.compose_file,
            config_root=plan.config_root,
            data_root=plan.data_root,
            media_root=plan.media_root,
            k8s_namespace=plan.k8s_namespace,
            dry_run=plan.dry_run,
            assume_yes=plan.assume_yes,
            environment=plan.environment,
            confirmation_token=plan.confirmation_token,
            actions=tuple(actions),
        )

    def path_is_protected(self, path: Path | None, plan: TeardownPlan) -> bool:
        if path is None:
            return False
        protected = plan.config_root / "defaults"
        try:
            path.resolve().relative_to(protected.resolve())
            return True
        except ValueError:
            return path.resolve() == protected.resolve()
