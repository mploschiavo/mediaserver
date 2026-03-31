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
    enable_unpackerr: str
    config_file: Path


@dataclass
class RebuildPipelineService:
    cfg: RebuildPipelineConfig
    info: InfoFn
    run_script: RunScriptFn

    def prepare_host_directories(self, storage_mode: str) -> bool:
        if storage_mode != "legacy-hostpath":
            self.info(f"Skipping host directory prep (storage mode: {storage_mode})")
            return False
        self.info(f"Preparing host directories under {self.cfg.prepare_host_root}")
        self.run_script("prepare-host.sh", self.cfg.prepare_host_root)
        return True

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
        self.run_script("apply-scale-policy.sh", env={"NAMESPACE": self.cfg.namespace})

    def run_bootstrap_pipeline(self) -> None:
        self.info("Running full bootstrap pipeline")
        self.run_script(
            "bootstrap-all.sh",
            str(self.cfg.config_file),
            env={
                "NAMESPACE": self.cfg.namespace,
                "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                "ENABLE_UNPACKERR": self.cfg.enable_unpackerr,
            },
        )
