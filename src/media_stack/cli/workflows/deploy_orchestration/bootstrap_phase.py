"""DeployBootstrapPhase — Command class for secrets + bootstrap pipeline.

ADR-0015 Phase 4. Pre-Phase-4 these methods lived on
``RunnerPhasesMixin``. The split groups every phase that touches
secret material or runs the bootstrap pipeline itself: secret
backup before namespace delete, secret restore after manifests
are applied, secret-generation on rebuild, scale-policy
guardrails, and the platform-specific bootstrap entry point.

Command pattern: each public method is a phase action invoked
via :meth:`DeployPipelineRunner._run_phase`. Phases that the
profile doesn't request raise :class:`SkipPhase`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from media_stack.cli.workflows.deploy_errors import DeployError, SkipPhase

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )
    from media_stack.cli.workflows.deploy_orchestration.deploy_service_factories import (
        DeployServiceFactoryBundle,
    )
    from media_stack.cli.workflows.deploy_orchestration.platform_adapter_factory import (
        PlatformAdapterFactory,
    )


class DeployBootstrapPhase:
    """Command set: backup + restore secrets, generate, run platform bootstrap."""

    def __init__(
        self,
        cfg: "DeployStackConfig",
        services: "DeployServiceFactoryBundle",
        platform_factory: "PlatformAdapterFactory",
        info_fn: Callable[[str], None],
        runner: object,
    ) -> None:
        self._cfg = cfg
        self._services = services
        self._platform_factory = platform_factory
        self._info_fn = info_fn
        self._runner = runner

    def apply_profile_defaults(self) -> dict[str, str]:
        """Resolve + apply profile defaults; return the resolved values.

        The caller writes the resolved values back onto cfg so the
        rest of the pipeline sees the merged view.
        """
        try:
            resolved = self._services.profile_defaults_service().apply(
                profile=self._cfg.profile,
                include_optional=self._cfg.include_optional,
                enable_components=self._cfg.enable_components,
                run_bootstrap=self._cfg.run_bootstrap,
            )
        except RuntimeError as exc:
            raise DeployError(str(exc)) from exc
        return {
            "include_optional": resolved.include_optional,
            "enable_components": resolved.enable_components,
            "run_bootstrap": resolved.run_bootstrap,
        }

    def backup_existing_secret_values(self) -> dict[str, str]:
        return self._platform_factory.adapter(self._runner).backup_secret_values(
            self._cfg.preserve_secret_on_rebuild,
        )

    def restore_secret_values_from_backup(
        self, backup_secret_values: dict[str, str],
    ) -> None:
        self._platform_factory.adapter(self._runner).restore_secret_values(
            backup_secret_values,
        )

    def generate_secrets(self) -> None:
        self._services.pipeline_service().generate_secrets()

    def apply_scale_policy_guardrails(self) -> None:
        self._services.pipeline_service().apply_scale_policy_guardrails()

    def skip_scale_policy_guardrails(self) -> None:
        self._info_fn("Scale-policy guardrails skipped for non-bootstrap profile.")
        raise SkipPhase()

    def run_platform_bootstrap(self) -> None:
        try:
            self._platform_factory.platform_plugin().run_bootstrap(self._runner)
        except RuntimeError as exc:
            raise DeployError(str(exc)) from exc

    def skip_bootstrap_pipeline(self) -> None:
        self._info_fn("Bootstrap skipped by profile/policy.")
        raise SkipPhase()


__all__ = ["DeployBootstrapPhase"]
