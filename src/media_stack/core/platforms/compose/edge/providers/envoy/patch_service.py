"""Compose Envoy patching helper.

Generates Envoy static runtime config from compose service routing labels so
provider swaps remain declarative and repeatable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from media_stack.core.platforms.compose.edge.providers.envoy.dynamic_config import EnvoyDynamicConfigService
from media_stack.core.platforms.compose.services.labels import ComposeLabelService
from media_stack.core.platforms.compose.services.runtime_artifacts import ComposeRuntimeArtifactService
from media_stack.core.platforms.compose.services.spec import ComposeSpecResolver


@dataclass(frozen=True)
class ComposeEnvoyPatchResult:
    applied: bool
    config_file: Path | None = None
    route_count: int = 0
    cluster_count: int = 0
    ignored_redirect_middleware_count: int = 0
    reason: str = ""


@dataclass
class ComposeEnvoyPatchService:
    label_service: ComposeLabelService
    spec_resolver: ComposeSpecResolver
    dynamic_config_service: EnvoyDynamicConfigService
    artifacts_service: ComposeRuntimeArtifactService
    info: Callable[[str], None]

    def apply_config_patch(
        self,
        services: dict[str, dict[str, Any]],
    ) -> ComposeEnvoyPatchResult:
        config_root = self.spec_resolver.config_root()
        if config_root is None:
            self.info("Compose Envoy patch skipped: CONFIG_ROOT/COMPOSE_CONFIG_ROOT not set.")
            return ComposeEnvoyPatchResult(applied=False, reason="missing-config-root")

        rendered = self.dynamic_config_service.render(services)
        config_file = config_root / "envoy" / "envoy.yaml"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            yaml.safe_dump(rendered.payload, sort_keys=False),
            encoding="utf-8",
        )

        self.artifacts_service.write_yaml_artifact(
            "resolved/envoy.runtime.yaml",
            rendered.payload,
            label="Compose Envoy runtime config artifact",
        )
        self.info(
            "Compose Envoy config applied automatically: "
            f"{config_file} "
            f"(routes={rendered.route_count}, clusters={rendered.cluster_count}, "
            f"ignored_redirect_middleware={rendered.ignored_redirect_middleware_count})."
        )
        return ComposeEnvoyPatchResult(
            applied=True,
            config_file=config_file,
            route_count=rendered.route_count,
            cluster_count=rendered.cluster_count,
            ignored_redirect_middleware_count=rendered.ignored_redirect_middleware_count,
        )
