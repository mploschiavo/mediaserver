"""Compose runtime artifact helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.platform_adapter import InfoFn


@dataclass
class ComposeRuntimeArtifactService:
    runtime_artifacts_dir: Path | None
    info: InfoFn

    def _base_dir(self) -> Path | None:
        out = self.runtime_artifacts_dir
        if out is None:
            return None
        out.mkdir(parents=True, exist_ok=True)
        return out

    def write_text_artifact(self, relative_path: str, text: str, *, label: str) -> Path | None:
        base = self._base_dir()
        if base is None:
            return None
        out = base / relative_path
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = text if text.endswith("\n") else f"{text}\n"
        out.write_text(payload, encoding="utf-8")
        self.info(f"{label}: {out}")
        return out

    def write_yaml_artifact(
        self,
        relative_path: str,
        payload: dict[str, Any],
        *,
        label: str,
    ) -> Path | None:
        text = yaml.safe_dump(payload, sort_keys=False)
        return self.write_text_artifact(relative_path, text, label=label)

    def write_json_artifact(
        self,
        relative_path: str,
        payload: dict[str, Any],
        *,
        label: str,
    ) -> Path | None:
        text = json.dumps(payload, indent=2, sort_keys=True)
        return self.write_text_artifact(relative_path, text, label=label)
