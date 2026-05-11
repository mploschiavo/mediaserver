"""DeployPipelineRunner — Composition Root + Template Method for the deploy.

ADR-0015 Phase 4. The orchestration entry point. Composes all
the SRP classes in this sub-package + the workflows-tier
:class:`DeployConfigService` Facade, then runs the deploy as a
sequence of tracked phases.

Composition Root pattern: this class is the one place that wires
the dependency graph for a deploy. Sub-services are constructor-
injected at the top of ``__init__``; phases reference each other
through these fields, never through globals or kwargs threaded
through 25 method calls.

Template Method pattern: ``run()`` is the fixed deploy template.
Each ``_run_phase("name", fn)`` call delegates to a Command in
one of the phase classes (manifest/bootstrap/verify); the
template itself owns no business logic, only the phase order and
the banner/notification surface.

Pre-Phase-4 the runner was ``DeployStackRunner`` composed via
two mixins (``RunnerServicesMixin``, ``RunnerPhasesMixin``) under
``cli/commands/``. Phase 4 moved the implementation here;
``DeployStackRunner`` in ``cli/commands/deploy_stack_main.py``
survives as a thin subclass for test-patch compatibility.
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from media_stack.cli.workflows.deploy_config import DeployConfigService
from media_stack.cli.workflows.deploy_errors import DeployError, SkipPhase
from media_stack.cli.workflows.deploy_orchestration.banner_logger import (
    DeployBannerLogger,
)
from media_stack.cli.workflows.deploy_orchestration.bootstrap_phase import (
    DeployBootstrapPhase,
)
from media_stack.cli.workflows.deploy_orchestration.deploy_service_factories import (
    DeployServiceFactoryBundle,
)
from media_stack.cli.workflows.deploy_orchestration.k8s_manifest_capturer import (
    K8sManifestCapturer,
)
from media_stack.cli.workflows.deploy_orchestration.manifest_phase import (
    DeployManifestPhase,
)
from media_stack.cli.workflows.deploy_orchestration.phase_validator import (
    DeployPhaseValidator,
)
from media_stack.cli.workflows.deploy_orchestration.platform_adapter_factory import (
    PlatformAdapterFactory,
)
from media_stack.cli.workflows.deploy_orchestration.runtime_artifact_writer import (
    RuntimeArtifactWriter,
)
from media_stack.cli.workflows.deploy_orchestration.runtime_options import (
    DeployRuntimeOptions,
)
from media_stack.cli.workflows.deploy_orchestration.verify_phase import (
    DeployVerifyPhase,
)
from media_stack.core.cli_common import info, warn
from media_stack.core.phase_tracker import PhaseTracker
from media_stack.core.subprocess_utils import CommandResult


if TYPE_CHECKING:
    from pathlib import Path

    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )


@dataclass
class DeployPipelineRunner:
    """Composition Root + Template Method for the deploy/bootstrap pipeline."""

    cfg: "DeployStackConfig"
    kube: Any | None = None
    tracker: PhaseTracker = field(default_factory=lambda: PhaseTracker(info=info, warn=warn))
    backup_secret_values: dict[str, str] = field(default_factory=dict)
    info_fn: Callable[[str], None] = info

    runtime_options: DeployRuntimeOptions = field(init=False, repr=False)
    config_service: DeployConfigService = field(init=False, repr=False)
    services: DeployServiceFactoryBundle = field(init=False, repr=False)
    platform_factory: PlatformAdapterFactory = field(init=False, repr=False)
    artifact_writer: RuntimeArtifactWriter = field(init=False, repr=False)
    k8s_capturer: K8sManifestCapturer = field(init=False, repr=False)
    validator: DeployPhaseValidator = field(init=False, repr=False)
    banner_logger: DeployBannerLogger = field(init=False, repr=False)
    manifest_phase: DeployManifestPhase = field(init=False, repr=False)
    bootstrap_phase: DeployBootstrapPhase = field(init=False, repr=False)
    verify_phase: DeployVerifyPhase = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Composition root: wire the dependency graph for a deploy.
        # Order matters — later collaborators reference earlier ones.
        self.runtime_options = DeployRuntimeOptions(self.cfg)
        self.config_service = DeployConfigService(self.cfg)
        self.platform_factory = PlatformAdapterFactory(
            self.cfg, self.runtime_options, self.info_fn,
        )
        self.artifact_writer = RuntimeArtifactWriter(
            self.cfg, self.runtime_options, self.config_service, self.info_fn,
        )
        self.k8s_capturer = K8sManifestCapturer(self.artifact_writer, self.tracker)
        self.services = DeployServiceFactoryBundle(
            self.cfg,
            self.runtime_options,
            self.config_service,
            run_script_callback=self._run_script,
        )
        self.validator = DeployPhaseValidator(
            self.cfg, self.config_service, self.platform_factory, self.runtime_options,
        )
        self.banner_logger = DeployBannerLogger(self.cfg, self.runtime_options)
        self.manifest_phase = DeployManifestPhase(
            self.services, self.platform_factory, self.runtime_options, self,
        )
        self.bootstrap_phase = DeployBootstrapPhase(
            self.cfg, self.services, self.platform_factory, self.info_fn, self,
        )
        self.verify_phase = DeployVerifyPhase(
            self.cfg,
            self.platform_factory,
            self.runtime_options,
            self.info_fn,
            self.kube,
            self,
        )

    # -- orchestration entry point -----------------------------------------

    def run(self) -> int:
        self.validator.validate()
        self.artifact_writer.initialize_run()
        self.platform_factory.configure_runtime(self)
        target = self.runtime_options.resolved_platform_target()
        platform_plugin = self.platform_factory.platform_plugin()

        info("Starting full media-stack deploy/bootstrap")
        self._run_phase("Resolve profile defaults", self._apply_profile_defaults)
        self.banner_logger.log(target, platform_plugin)
        self.notify(
            "info",
            f"media-stack deploy/bootstrap started (profile={self.cfg.profile}, namespace={self.cfg.namespace})",
        )

        self._run_pre_bootstrap_phases(platform_plugin)
        self._run_bootstrap_phases(platform_plugin)
        self._run_post_bootstrap_phases()

        self.tracker.summary()
        print("\n[OK] Rebuild + bootstrap completed.")
        self.notify(
            "ok",
            f"media-stack deploy/bootstrap succeeded (profile={self.cfg.profile}, namespace={self.cfg.namespace})",
        )
        return 0

    def _run_pre_bootstrap_phases(self, platform_plugin) -> None:
        self._run_phase(
            "Validate bootstrap config schema",
            lambda: self._run_script(
                "validate-bootstrap-config.sh", "--config", str(self.cfg.config_file)
            ),
        )
        if self.cfg.skip_prepare_host != "1":
            self._run_phase(
                "Prepare host directories",
                lambda: self.manifest_phase.prepare_host_directories(self.cfg.storage_mode),
            )
        else:
            self._run_phase("Prepare host directories", lambda: None, enabled=False)

        self._run_phase(
            "Backup existing credentials",
            self._backup_existing_secret_values,
            enabled=platform_plugin.supports_secret_lifecycle,
        )
        self._run_phase("Delete namespace (optional)", self.manifest_phase.delete_namespace_optional)
        self._run_phase("Apply manifests for profile", self.manifest_phase.apply_manifests_for_profile)

        if (
            platform_plugin.supports_secret_generation
            and self.cfg.generate_secrets_on_rebuild == "1"
        ):
            self._run_phase("Generate secrets", self.bootstrap_phase.generate_secrets)
        else:
            self._run_phase("Generate secrets", lambda: None, enabled=False)

        self._run_phase(
            "Restore preserved credentials",
            self._restore_secret_values_from_backup,
            enabled=platform_plugin.supports_secret_lifecycle,
        )
        self._run_phase(
            "Patch ingress class",
            self.manifest_phase.patch_ingress_class,
            enabled=platform_plugin.supports_ingress_patch,
        )
        self._run_phase("Wait for deployments", self.verify_phase.wait_for_deployments)

    def _run_bootstrap_phases(self, platform_plugin) -> None:
        if self.cfg.run_bootstrap == "1":
            if platform_plugin.supports_scale_policy_guardrails:
                self._run_phase(
                    "Apply scale-policy guardrails",
                    self.bootstrap_phase.apply_scale_policy_guardrails,
                )
            else:
                self._run_phase(
                    "Apply scale-policy guardrails",
                    self.bootstrap_phase.skip_scale_policy_guardrails,
                    enabled=True,
                )
            self._run_phase(
                platform_plugin.bootstrap_phase_name,
                self.bootstrap_phase.run_platform_bootstrap,
            )
        else:
            self._run_phase(
                "Apply scale-policy guardrails",
                self.bootstrap_phase.skip_scale_policy_guardrails,
                enabled=True,
            )
            self._run_phase(
                platform_plugin.bootstrap_phase_name,
                self.bootstrap_phase.skip_bootstrap_pipeline,
            )

    def _run_post_bootstrap_phases(self) -> None:
        if self.cfg.run_smoke_test == "1":
            self._run_phase("Run ingress smoke test", self._run_smoke_test)
        else:
            self._run_phase("Run ingress smoke test", lambda: None, enabled=False)
        if self.runtime_options.is_truthy(self.cfg.chaos_enabled):
            self._run_phase("Run chaos recovery tests", self.verify_phase.run_chaos_tests)
        else:
            self._run_phase("Run chaos recovery tests", lambda: None, enabled=False)
        self._run_phase("Collect final pod status", self.verify_phase.print_final_pod_status)

    # -- phase-runner helper -----------------------------------------------

    def _run_phase(self, name: str, fn: Callable[[], None], *, enabled: bool = True) -> None:
        self.tracker.start(name)
        if not enabled:
            self.tracker.end("skipped")
            return
        try:
            fn()
            self.tracker.end("ok")
        except SkipPhase:
            self.tracker.end("skipped")
        except Exception:
            self.tracker.end("failed")
            raise

    # -- thin orchestrator wrappers ----------------------------------------

    def _run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        try:
            self.services.script_runner_service().run_script(script_name, *args, env=env)
        except RuntimeError as exc:
            raise DeployError(str(exc)) from exc

    def _run_kubectl(
        self,
        args: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> CommandResult:
        if self.kube is None:
            raise DeployError("Kubernetes client not configured for this platform target.")
        if input_text is not None and self.k8s_capturer.is_apply_with_stdin(args):
            self.k8s_capturer.record(args=args, manifest_text=input_text)
        proc = self.kube.run(args, check=False, input_text=input_text)
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if check and proc.returncode != 0:
            raise DeployError(
                f"Kubernetes command failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in proc.args)}"
            )
        return proc

    def notify(self, status: str, message: str) -> None:
        self.services.notification_service().notify(status, message)

    def emit_failure_status_snapshot(self) -> None:
        self.verify_phase.emit_failure_status_snapshot()

    # -- phase wrappers that write back to cfg / runner --------------------

    def _apply_profile_defaults(self) -> None:
        resolved = self.bootstrap_phase.apply_profile_defaults()
        self.cfg.include_optional = resolved["include_optional"]
        self.cfg.enable_components = resolved["enable_components"]
        self.cfg.run_bootstrap = resolved["run_bootstrap"]

    def _backup_existing_secret_values(self) -> None:
        self.backup_secret_values = self.bootstrap_phase.backup_existing_secret_values()

    def _restore_secret_values_from_backup(self) -> None:
        self.bootstrap_phase.restore_secret_values_from_backup(self.backup_secret_values)

    def _run_smoke_test(self) -> None:
        resolved = self.verify_phase.run_smoke_test()
        self.cfg.node_ip = resolved or self.cfg.node_ip

    # -- test-surface compatibility shims ----------------------------------
    # Pre-Phase-4 tests address the legacy mixin methods. Each shim is
    # a 1-liner that forwards into the matching SRP collaborator.

    def _is_truthy(self, value: str) -> bool:
        return self.runtime_options.is_truthy(value)

    def _compose_profiles(self) -> tuple[str, ...]:
        return self.runtime_options.compose_profiles()

    def _selected_apps(self) -> tuple[str, ...]:
        return self.runtime_options.selected_apps()

    def _auth_provider_service_names(self) -> tuple[str, ...]:
        return self.runtime_options.auth_provider_service_names()

    def _chaos_actions(self) -> tuple[str, ...]:
        return self.runtime_options.chaos_actions()

    def _delete_environment_requested(self) -> bool:
        return self.runtime_options.delete_environment_requested()

    def _delete_environment_confirmation_target(self) -> str:
        return self.runtime_options.delete_environment_confirmation_target()

    def _delete_environment_enabled(self) -> bool:
        return self.runtime_options.delete_environment_enabled()

    def _resolved_platform_target(self) -> str:
        return self.runtime_options.resolved_platform_target()

    def _platform_plugin(self):
        return self.platform_factory.platform_plugin()

    def _platform_adapter(self):
        return self.platform_factory.adapter(self)

    def get_or_create_platform_client(self, key: str, factory: Callable[[], object]) -> object:
        return self.platform_factory.get_or_create_client(key, factory)

    def _validate_inputs(self) -> None:
        self.validator.validate()

    def _is_k8s_apply_with_stdin(self, args: list[str]) -> bool:
        return self.k8s_capturer.is_apply_with_stdin(args)

    @property
    def runtime_artifacts_root(self) -> "Path | None":
        return self.artifact_writer.root

    @runtime_artifacts_root.setter
    def runtime_artifacts_root(self, value: "Path | None") -> None:
        self.artifact_writer.root = value

    def runtime_artifacts_target_dir(self, target: str) -> "Path | None":
        return self.artifact_writer.target_dir(target)

    def _runtime_artifacts_target_dir(self, target: str) -> "Path | None":
        return self.artifact_writer.target_dir(target)

    def _write_runtime_artifact_text(
        self, target, relative_path, text, *, label, log=True,
    ):
        return self.artifact_writer.write_text(
            target=target, relative_path=relative_path, text=text, label=label, log=log,
        )

    def _write_runtime_artifact_json(
        self, target, relative_path, payload, *, label, log=True,
    ):
        return self.artifact_writer.write_json(
            target=target, relative_path=relative_path, payload=payload, label=label, log=log,
        )

    def _initialize_runtime_artifacts(self) -> None:
        self.artifact_writer.initialize_run()


__all__ = ["DeployPipelineRunner"]
