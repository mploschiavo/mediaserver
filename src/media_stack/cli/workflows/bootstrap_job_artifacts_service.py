from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True)
class BootstrapJobArtifacts:
    job_log_file: Path
    job_config_file: Path


class BootstrapJobArtifactsService:
    def create(self) -> BootstrapJobArtifacts:
        return BootstrapJobArtifacts(
            job_log_file=Path(
                NamedTemporaryFile(
                    prefix="media-stack-controller-log.",
                    suffix=".log",
                    delete=False,
                ).name
            ),
            job_config_file=Path(
                NamedTemporaryFile(
                    prefix="media-stack-controller-config.",
                    suffix=".json",
                    delete=False,
                ).name
            ),
        )

    def cleanup(self, artifacts: BootstrapJobArtifacts) -> None:
        for file_path in (artifacts.job_log_file, artifacts.job_config_file):
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass
