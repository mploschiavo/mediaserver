from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
import logging


@dataclass(frozen=True)
class ControllerJobArtifacts:
    job_log_file: Path
    job_config_file: Path


class ControllerJobArtifactsService:
    def create(self) -> ControllerJobArtifacts:
        return ControllerJobArtifacts(
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

    def cleanup(self, artifacts: ControllerJobArtifacts) -> None:
        for file_path in (artifacts.job_log_file, artifacts.job_config_file):
            try:
                file_path.unlink(missing_ok=True)
            except Exception as exc:
                log_swallowed(exc)
