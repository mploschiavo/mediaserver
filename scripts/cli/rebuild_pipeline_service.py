"""Scripted rebuild pipeline step helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

InfoFn = Callable[[str], None]
RunScriptFn = Callable[..., None]


@dataclass(frozen=True)
class RebuildPipelineConfig:
    namespace: str
    root_dir: Path
    prepare_host_root: str
    enable_components: str
    config_file: Path


@dataclass
class RebuildPipelineService:
    cfg: RebuildPipelineConfig
    info: InfoFn
    run_script: RunScriptFn

    def prepare_host_directories(self, storage_mode: str) -> bool:
        self.info(
            "Skipping host directory prep (dynamic PVC mode only; "
            f"requested storage mode: {storage_mode})."
        )
        return False

    def generate_secrets(self) -> None:
        self.info("Generating secure secrets in cluster before bootstrap")
        self.run_script(
            "generate-secrets.sh",
            env={
                "NAMESPACE": self.cfg.namespace,
                "OUTPUT_FILE": str(self.cfg.root_dir / "secrets.generated.env"),
            },
        )

    def apply_scale_policy_guardrails(self) -> None:
        self.info("Applying scale-policy guardrails")
        self.run_script(
            "apply-scale-policy.sh",
            str(self.cfg.config_file),
            env={"NAMESPACE": self.cfg.namespace},
        )

    def run_bootstrap_pipeline(self) -> None:
        self.info("Running full bootstrap pipeline")
        self.run_script(
            "bootstrap-all.sh",
            str(self.cfg.config_file),
            env={
                "NAMESPACE": self.cfg.namespace,
                "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                "ENABLE_COMPONENTS": self.cfg.enable_components,
            },
        )
