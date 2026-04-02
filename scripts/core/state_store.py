"""Small JSON-backed checkpoint state store."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CheckpointStateStore:
    path: Path
    schema_version: int = 1
    data: dict[str, Any] = field(default_factory=dict)

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data = loaded
            except Exception:
                self.data = {}
        if not isinstance(self.data.get("phases"), dict):
            self.data["phases"] = {}
        self.data.setdefault("schema", self.schema_version)
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["schema"] = self.schema_version
        self.data["updated_at_epoch"] = int(time.time())
        self.path.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")

    def clear(self) -> None:
        self.data = {"schema": self.schema_version, "phases": {}}
        self.save()

    def phase_status(self, phase_name: str) -> str:
        phases = self.data.get("phases") or {}
        phase = phases.get(str(phase_name)) or {}
        return str(phase.get("status") or "")

    def is_phase_done(self, phase_name: str) -> bool:
        return self.phase_status(phase_name) == "ok"

    def mark_phase(self, phase_name: str, status: str, **details: Any) -> None:
        phases = self.data.setdefault("phases", {})
        entry = {
            "status": str(status or "").strip().lower() or "unknown",
            "ts_epoch": int(time.time()),
        }
        if details:
            entry["details"] = details
        phases[str(phase_name)] = entry
        self.save()
