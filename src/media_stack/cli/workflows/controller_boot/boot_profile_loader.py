"""BootProfileLoader — Repository for the boot-time bootstrap profile YAML.

ADR-0015 Phase 7e. Pre-Phase-7e the ``_load_boot_profile`` helper
lived as a method on ``ControllerServeCommand`` in commands/. The
env-sample-once-then-read pattern matches ADR-0012's
``OS_ENVIRON_IN_METHODS_RATCHET`` guidance: the env dict is
passed in by the caller so the method never reads ``os.environ``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml as _yaml


class BootProfileLoader:
    """Repository: best-effort read of the boot-time profile YAML."""

    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log

    def load(self, env: dict) -> dict:
        """Best-effort profile load for the boot configure-auth step.

        Reads ``BOOTSTRAP_PROFILE_FILE`` from the passed env dict so the
        env is sampled exactly once at boot and the method stays off
        ``os.environ`` (the class-structure ratchet).
        """
        pf = str(env.get("BOOTSTRAP_PROFILE_FILE", "")).strip()
        if not pf:
            return {}
        path = Path(pf)
        if not path.is_file():
            return {}
        try:
            data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (_yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            self._log(
                f"[DEBUG] boot configure-auth: profile load failed: {exc}",
            )
            return {}
        return data if isinstance(data, dict) else {}


__all__ = ["BootProfileLoader"]
