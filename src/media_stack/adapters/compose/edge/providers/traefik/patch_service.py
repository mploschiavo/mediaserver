"""Compose Traefik patching helper.

Handles automatic file-provider patch generation so edge runtime updates are
declarative and repeatable across deploy runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from media_stack.adapters.compose.services.edge_route_graph import ComposeEdgeRouteGraphService
from media_stack.adapters.compose.services.labels import ComposeLabelService
from media_stack.adapters.compose.services.runtime_artifacts import ComposeRuntimeArtifactService
from media_stack.adapters.compose.services.spec import ComposeSpecResolver


@dataclass(frozen=True)
class ComposeTraefikPatchResult:
    applied: bool
    dynamic_file: Path | None = None
    router_count: int = 0
    service_count: int = 0
    middleware_count: int = 0
    reason: str = ""


@dataclass
class ComposeTraefikPatchService:
    label_service: ComposeLabelService
    spec_resolver: ComposeSpecResolver
    dynamic_config_service: ComposeEdgeRouteGraphService
    artifacts_service: ComposeRuntimeArtifactService
    info: Callable[[str], None]

    def apply_dynamic_file_patch(
        self,
        services: dict[str, dict[str, Any]],
    ) -> ComposeTraefikPatchResult:
        config_root = self.spec_resolver.config_root()
        if config_root is None:
            self.info("Compose Traefik patch skipped: CONFIG_ROOT/COMPOSE_CONFIG_ROOT not set.")
            return ComposeTraefikPatchResult(applied=False, reason="missing-config-root")

        rendered = self.dynamic_config_service.render(services)
        dynamic_file = config_root / "traefik" / "dynamic" / "media-stack.dynamic.yaml"
        dynamic_file.parent.mkdir(parents=True, exist_ok=True)
        dynamic_file.write_text(
            yaml.safe_dump(rendered.payload, sort_keys=False),
            encoding="utf-8",
        )

        self.artifacts_service.write_yaml_artifact(
            "resolved/traefik.dynamic.runtime.yaml",
            rendered.payload,
            label="Compose Traefik dynamic config artifact",
        )
        self.info(
            "Compose Traefik patch applied automatically: "
            f"{dynamic_file} "
            f"(routers={rendered.router_count}, services={rendered.service_count}, "
            f"middlewares={rendered.middleware_count})."
        )
        return ComposeTraefikPatchResult(
            applied=True,
            dynamic_file=dynamic_file,
            router_count=rendered.router_count,
            service_count=rendered.service_count,
            middleware_count=rendered.middleware_count,
        )
