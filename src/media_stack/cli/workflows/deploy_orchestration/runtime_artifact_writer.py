"""RuntimeArtifactWriter — Repository for per-run artifact directories.

ADR-0015 Phase 4. Pre-Phase-4 the artifact-write methods lived on
``RunnerServicesMixin`` (a god-mixin in commands/). The shape is
classic Repository: open a per-run directory under
``<root>/.state/runtime-artifacts/<run-id>/`` and write text or
JSON payloads relative to that root, capturing the artifact path
for the operator log.

The writer owns ``root`` (None until ``initialize_run()`` is called;
non-None thereafter). The lazy initialization is deliberate — unit
tests that exercise individual writes set ``root`` directly via
the property below, while the full pipeline path goes through
``initialize_run()`` and gets the timestamped run-id structure.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from media_stack.core.cli_common import ts

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )
    from media_stack.cli.workflows.deploy_orchestration.runtime_options import (
        DeployRuntimeOptions,
    )
    from media_stack.cli.workflows.deploy_config import DeployConfigService


class RuntimeArtifactWriter:
    """Repository: write per-run artifact files under a timestamped root.

    ``root`` is None until ``initialize_run()`` is called. While
    None, ``target_dir()`` returns None and the write methods
    no-op gracefully. Once initialized, ``target_dir(target)``
    creates and returns the ``<root>/<target>/`` directory.
    """

    def __init__(
        self,
        cfg: "DeployStackConfig",
        runtime_options: "DeployRuntimeOptions",
        config_service: "DeployConfigService",
        info_fn: Callable[[str], None],
    ) -> None:
        self._cfg = cfg
        self._runtime_options = runtime_options
        self._config_service = config_service
        self._info_fn = info_fn
        self._root: Path | None = None

    @property
    def root(self) -> Path | None:
        return self._root

    @root.setter
    def root(self, value: Path | None) -> None:
        # Tests assign ``root`` directly to a temp directory; the
        # property accepts the override so the lazy-initialise path
        # can be skipped in unit-test fixtures.
        self._root = value

    def initialize_run(self) -> None:
        target = self._runtime_options.resolved_platform_target()
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        namespace_token = str(self._cfg.namespace or "").strip().replace("/", "-")
        run_id = f"{timestamp}-{target}-{namespace_token}"
        root = self._cfg.root_dir / ".state" / "runtime-artifacts" / run_id
        root.mkdir(parents=True, exist_ok=True)
        self._root = root
        self._info_fn(f"Runtime artifact root: {root}")
        self.write_json(
            target="shared",
            relative_path="run-context.json",
            payload={
                "created_at": ts(),
                "platform_target": target,
                "namespace": self._cfg.namespace,
                "profile": self._cfg.profile,
                "purpose": self._cfg.purpose,
                "bootstrap_config_file": str(self._cfg.config_file),
                "bootstrap_profile_file": (
                    str(self._cfg.bootstrap_profile_file)
                    if self._cfg.bootstrap_profile_file
                    else ""
                ),
                "route_strategy": self._cfg.route_strategy,
                "auth_provider": self._cfg.auth_provider,
                "edge_router_provider": self._config_service.edge_router_provider(),
                "run_bootstrap": self._cfg.run_bootstrap,
                "run_smoke_test": self._cfg.run_smoke_test,
            },
            label="Wrote runtime artifact run context",
        )

    def target_dir(self, target: str) -> Path | None:
        if self._root is None:
            return None
        token = str(target or "").strip().lower() or "shared"
        out = self._root / token
        out.mkdir(parents=True, exist_ok=True)
        return out

    def write_text(
        self,
        target: str,
        relative_path: str,
        text: str,
        *,
        label: str,
        log: bool = True,
    ) -> Path | None:
        base = self.target_dir(target)
        if base is None:
            return None
        out = base / relative_path
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = text if text.endswith("\n") else f"{text}\n"
        out.write_text(payload, encoding="utf-8")
        if log:
            self._info_fn(f"{label}: {out}")
        return out

    def write_json(
        self,
        target: str,
        relative_path: str,
        payload: dict[str, object],
        *,
        label: str,
        log: bool = True,
    ) -> Path | None:
        text = json.dumps(payload, indent=2, sort_keys=True)
        return self.write_text(
            target=target,
            relative_path=relative_path,
            text=text,
            label=label,
            log=log,
        )


__all__ = ["RuntimeArtifactWriter"]
