#!/usr/bin/env python3
"""Python CLI for rebuild-and-bootstrap orchestration.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.kube import resolve_kubectl_binary
from core.phase_tracker import PhaseTracker

from cli.bootstrap_notification_service import (
    BootstrapNotificationConfig,
    BootstrapNotificationService,
)
from cli.rebuild_cli_config_service import (
    RebuildBootstrapConfig,
    parse_rebuild_bootstrap_config,
)
from cli.rebuild_deployments_wait_service import (
    RebuildDeploymentsWaitConfig,
    RebuildDeploymentsWaitService,
)
from cli.rebuild_ingress_service import RebuildIngressConfig, RebuildIngressService
from cli.rebuild_manifest_apply_service import (
    RebuildManifestApplyConfig,
    RebuildManifestApplyService,
)
from cli.rebuild_manifest_overrides_service import (
    RebuildManifestOverridesConfig,
    RebuildManifestOverridesService,
)
from cli.rebuild_namespace_service import RebuildNamespaceConfig, RebuildNamespaceService
from cli.rebuild_pipeline_service import RebuildPipelineConfig, RebuildPipelineService
from cli.rebuild_profile_defaults_service import RebuildProfileDefaultsService
from cli.rebuild_script_runner_service import (
    RebuildScriptRunnerConfig,
    RebuildScriptRunnerService,
)
from cli.rebuild_secret_preservation_service import (
    RebuildSecretPreservationConfig,
    RebuildSecretPreservationService,
)
from cli.rebuild_smoke_test_service import RebuildSmokeTestService


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr, flush=True)


class RebuildError(RuntimeError):
    """Raised when rebuild/bootstrap orchestration fails."""


class SkipPhase(RuntimeError):
    """Signal that current phase should be marked as skipped."""


@dataclass
class RebuildBootstrapRunner:
    cfg: RebuildBootstrapConfig
    kubectl: list[str]
    tracker: PhaseTracker = field(default_factory=lambda: PhaseTracker(info=info, warn=warn))
    backup_secret_values: dict[str, str] = field(default_factory=dict)

    def _notification_service(self) -> BootstrapNotificationService:
        return BootstrapNotificationService(
            cfg=BootstrapNotificationConfig(
                alert_webhook_url=self.cfg.alert_webhook_url,
            )
        )

    def _script_runner_service(self) -> RebuildScriptRunnerService:
        return RebuildScriptRunnerService(
            cfg=RebuildScriptRunnerConfig(
                root_dir=self.cfg.root_dir,
                namespace=self.cfg.namespace,
            )
        )

    def _secret_preservation_service(self) -> RebuildSecretPreservationService:
        return RebuildSecretPreservationService(
            cfg=RebuildSecretPreservationConfig(
                namespace=self.cfg.namespace,
                secret_name=self.cfg.secret_name,
                kubectl=self.kubectl,
            ),
            info=info,
            run_kubectl=self._run_kubectl,
        )

    def _namespace_service(self) -> RebuildNamespaceService:
        return RebuildNamespaceService(
            cfg=RebuildNamespaceConfig(
                namespace=self.cfg.namespace,
                kubectl=self.kubectl,
            ),
            info=info,
            run_kubectl=self._run_kubectl,
        )

    def _manifest_overrides_service(self) -> RebuildManifestOverridesService:
        return RebuildManifestOverridesService(
            cfg=RebuildManifestOverridesConfig(
                namespace=self.cfg.namespace,
                prepare_host_root=self.cfg.prepare_host_root,
                ingress_domain=self.cfg.ingress_domain,
                pvc_storage_class=self.cfg.pvc_storage_class,
            ),
            run_kubectl=self._run_kubectl,
        )

    def _manifest_apply_service(self) -> RebuildManifestApplyService:
        return RebuildManifestApplyService(
            cfg=RebuildManifestApplyConfig(
                root_dir=self.cfg.root_dir,
                namespace=self.cfg.namespace,
                profile=self.cfg.profile,
                include_optional=self.cfg.include_optional,
                enable_unpackerr=self.cfg.enable_unpackerr,
                kubectl=self.kubectl,
            ),
            info=info,
            warn=warn,
            run_kubectl=self._run_kubectl,
            apply_manifest_text_with_overrides=self._apply_manifest_text_with_overrides,
            apply_manifest_file_with_overrides=self._apply_manifest_file_with_overrides,
        )

    def _profile_defaults_service(self) -> RebuildProfileDefaultsService:
        return RebuildProfileDefaultsService()

    def _ingress_service(self) -> RebuildIngressService:
        return RebuildIngressService(
            cfg=RebuildIngressConfig(
                namespace=self.cfg.namespace,
                ingress_class=self.cfg.ingress_class,
                kubectl=self.kubectl,
            ),
            info=info,
            warn=warn,
            run_script=self._run_script,
        )

    def _deployments_wait_service(self) -> RebuildDeploymentsWaitService:
        return RebuildDeploymentsWaitService(
            cfg=RebuildDeploymentsWaitConfig(
                namespace=self.cfg.namespace,
                wait_timeout=self.cfg.wait_timeout,
                kubectl=self.kubectl,
            ),
            info=info,
            warn=warn,
        )

    def _pipeline_service(self) -> RebuildPipelineService:
        return RebuildPipelineService(
            cfg=RebuildPipelineConfig(
                namespace=self.cfg.namespace,
                root_dir=self.cfg.root_dir,
                prepare_host_root=self.cfg.prepare_host_root,
                enable_unpackerr=self.cfg.enable_unpackerr,
                config_file=self.cfg.config_file,
            ),
            info=info,
            run_script=self._run_script,
        )

    def _smoke_test_service(self) -> RebuildSmokeTestService:
        return RebuildSmokeTestService(
            namespace=self.cfg.namespace,
            node_ip=self.cfg.node_ip,
            info=info,
            warn=warn,
            run_script=self._run_script,
        )

    def run(self) -> int:
        self._validate_inputs()

        info("Starting full media-stack rebuild/bootstrap")
        self._run_phase("Resolve profile defaults", self.apply_profile_defaults)
        info(f"Namespace: {self.cfg.namespace}")
        info(f"Profile: {self.cfg.profile}")
        info(f"Ingress domain: {self.cfg.ingress_domain}")
        info(f"Config: {self.cfg.config_file}")
        info(f"Delete namespace: {self.cfg.delete_namespace}")
        info(f"Storage mode: {self.cfg.storage_mode}")
        if self.cfg.pvc_storage_class:
            info(f"PVC storage class override: {self.cfg.pvc_storage_class}")
        else:
            info("PVC storage class override: <cluster default>")
        info(f"Include optional: {self.cfg.include_optional}")
        info(f"Enable Unpackerr: {self.cfg.enable_unpackerr}")
        info(f"Run bootstrap: {self.cfg.run_bootstrap}")
        info(f"Generate secrets on rebuild: {self.cfg.generate_secrets_on_rebuild}")
        info(f"Preserve secret on rebuild: {self.cfg.preserve_secret_on_rebuild}")

        self.notify(
            "info",
            f"media-stack rebuild/bootstrap started (profile={self.cfg.profile}, namespace={self.cfg.namespace})",
        )

        self._run_phase(
            "Validate bootstrap config schema",
            lambda: self._run_script("validate-bootstrap-config.sh", "--config", str(self.cfg.config_file)),
        )

        if self.cfg.skip_prepare_host != "1":
            self._run_phase("Prepare host directories", self.prepare_host_directories)
        else:
            self._run_phase("Prepare host directories", lambda: None, enabled=False)

        self._run_phase("Backup existing credentials", self.backup_existing_secret_values)
        self._run_phase("Delete namespace (optional)", self.delete_namespace_optional)
        self._run_phase("Apply manifests for profile", self.apply_manifests_for_profile)

        if self.cfg.generate_secrets_on_rebuild == "1":
            self._run_phase("Generate secrets", self.generate_secrets)
        else:
            self._run_phase("Generate secrets", lambda: None, enabled=False)

        self._run_phase("Restore preserved credentials", self.restore_secret_values_from_backup)
        self._run_phase("Patch ingress class", self.patch_ingress_class)
        self._run_phase("Wait for deployments", self.wait_for_deployments)

        if self.cfg.run_bootstrap == "1":
            self._run_phase("Apply scale-policy guardrails", self.apply_scale_policy_guardrails)
            self._run_phase("Run bootstrap pipeline", self.run_bootstrap_pipeline)
        else:
            self._run_phase("Apply scale-policy guardrails", self.skip_scale_policy_guardrails, enabled=True)
            self._run_phase("Run bootstrap pipeline", self.skip_bootstrap_pipeline, enabled=True)

        if self.cfg.run_smoke_test == "1":
            self._run_phase("Run ingress smoke test", self.run_smoke_test)
        else:
            self._run_phase("Run ingress smoke test", lambda: None, enabled=False)

        self._run_phase("Collect final pod status", self.print_final_pod_status)
        self.tracker.summary()

        print("\n[OK] Rebuild + bootstrap completed.")
        self.notify(
            "ok",
            f"media-stack rebuild/bootstrap succeeded (profile={self.cfg.profile}, namespace={self.cfg.namespace})",
        )
        return 0

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

    def _validate_inputs(self) -> None:
        if not self.cfg.config_file.exists():
            raise RebuildError(f"Config file not found: {self.cfg.config_file}")
        if not self.cfg.namespace.strip():
            raise RebuildError("NAMESPACE cannot be empty.")
        self.cfg.ingress_domain = self.cfg.ingress_domain.lstrip(".").strip()
        if not self.cfg.ingress_domain:
            raise RebuildError("INGRESS_DOMAIN cannot be empty.")
        if self.cfg.storage_mode not in {"dynamic-pvc", "legacy-hostpath"}:
            raise RebuildError(
                f"Unsupported STORAGE_MODE '{self.cfg.storage_mode}'. Use dynamic-pvc|legacy-hostpath."
            )
        if self.cfg.profile not in {"minimal", "full", "public-demo", "power-user"}:
            raise RebuildError(
                f"Unknown PROFILE '{self.cfg.profile}'. Supported: minimal, full, public-demo, power-user."
            )

    def apply_profile_defaults(self) -> None:
        try:
            resolved = self._profile_defaults_service().apply(
                profile=self.cfg.profile,
                include_optional=self.cfg.include_optional,
                enable_unpackerr=self.cfg.enable_unpackerr,
                run_bootstrap=self.cfg.run_bootstrap,
            )
        except RuntimeError as exc:
            raise RebuildError(str(exc)) from exc
        self.cfg.include_optional = resolved.include_optional
        self.cfg.enable_unpackerr = resolved.enable_unpackerr
        self.cfg.run_bootstrap = resolved.run_bootstrap

    def _run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        try:
            self._script_runner_service().run_script(script_name, *args, env=env)
        except RuntimeError as exc:
            raise RebuildError(str(exc)) from exc

    def _run_kubectl(
        self,
        args: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [*self.kubectl, *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if check and proc.returncode != 0:
            raise RebuildError(
                f"kubectl command failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in [*self.kubectl, *args])}"
            )
        return proc

    def notify(self, status: str, message: str) -> None:
        self._notification_service().notify(status, message)

    def prepare_host_directories(self) -> None:
        handled = self._pipeline_service().prepare_host_directories(self.cfg.storage_mode)
        if not handled:
            raise SkipPhase()

    def backup_existing_secret_values(self) -> None:
        self.backup_secret_values = self._secret_preservation_service().backup_existing_values(
            self.cfg.preserve_secret_on_rebuild,
        )

    def restore_secret_values_from_backup(self) -> None:
        self._secret_preservation_service().restore_values(self.backup_secret_values)

    def delete_namespace_optional(self) -> None:
        handled = self._namespace_service().delete_namespace_optional(self.cfg.delete_namespace)
        if not handled:
            raise SkipPhase()

    def wait_for_namespace_deleted(self, max_wait_seconds: int = 600) -> None:
        try:
            self._namespace_service().wait_for_namespace_deleted(max_wait_seconds=max_wait_seconds)
        except RuntimeError as exc:
            raise RebuildError(str(exc)) from exc

    def _stream_with_manifest_overrides(self, text: str) -> str:
        return self._manifest_overrides_service().stream_with_manifest_overrides(text)

    def _inject_storage_class(self, text: str) -> str:
        return self._manifest_overrides_service().inject_storage_class(text)

    def _apply_manifest_text_with_overrides(self, text: str) -> None:
        self._manifest_overrides_service().apply_manifest_text_with_overrides(text)

    def _apply_manifest_file_with_overrides(self, file_path: Path) -> None:
        self._manifest_overrides_service().apply_manifest_file_with_overrides(file_path)

    def apply_manifests_for_profile(self) -> None:
        self._manifest_apply_service().apply_manifests_for_profile()

    def generate_secrets(self) -> None:
        self._pipeline_service().generate_secrets()

    def pick_ingress_class(self) -> str:
        return self._ingress_service().pick_ingress_class()

    def patch_ingress_class(self) -> None:
        handled = self._ingress_service().patch_ingress_class()
        if not handled:
            raise SkipPhase()

    def wait_for_deployments(self) -> None:
        try:
            self._deployments_wait_service().wait_for_deployments()
        except RuntimeError as exc:
            raise RebuildError(str(exc)) from exc

    def apply_scale_policy_guardrails(self) -> None:
        self._pipeline_service().apply_scale_policy_guardrails()

    def skip_scale_policy_guardrails(self) -> None:
        info("Scale-policy guardrails skipped for non-bootstrap profile.")
        raise SkipPhase()

    def run_bootstrap_pipeline(self) -> None:
        self._pipeline_service().run_bootstrap_pipeline()

    def skip_bootstrap_pipeline(self) -> None:
        info("Bootstrap skipped by profile/policy.")
        raise SkipPhase()

    def run_smoke_test(self) -> None:
        resolved = self._smoke_test_service().run_smoke_test()
        self.cfg.node_ip = resolved or self.cfg.node_ip
        if not resolved:
            raise SkipPhase()

    def print_final_pod_status(self) -> None:
        info("Final pod status:")
        self._run_kubectl(["-n", self.cfg.namespace, "get", "pods"])

def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root_dir = Path(__file__).resolve().parents[2]
    cfg = parse_rebuild_bootstrap_config(args, root_dir=root_dir)

    try:
        kubectl = resolve_kubectl_binary()
    except Exception as exc:
        err(str(exc))
        return 2

    runner = RebuildBootstrapRunner(cfg=cfg, kubectl=kubectl)
    try:
        return runner.run()
    except Exception as exc:
        warn(f"Rebuild/bootstrap failed: {exc}")
        warn("Pod status snapshot at failure:")
        subprocess.run([*kubectl, "-n", cfg.namespace, "get", "pods", "-o", "wide"], check=False)
        runner.tracker.summary()
        runner.notify(
            "error",
            f"media-stack rebuild/bootstrap failed (profile={cfg.profile}, namespace={cfg.namespace})",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
