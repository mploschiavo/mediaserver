"""OpenAPI YAML loading + servers-list rendering.

Lifted from ``media_stack.api.handlers_get`` during ADR-0007 Phase 2
Phase E (legacy-handler retirement).

The OpenAPI spec lives at ``contracts/api/openapi.yaml`` since
ADR-0001 Phase 4 (v1.0.195). At process start, the YAML text is
loaded once and kept in ``OPENAPI_YAML`` so every ``/api/openapi.yaml``
hit serves the same bytes -- no per-request disk I/O.

The ``ServersBuilder.build_servers()`` helper synthesises the OpenAPI
``servers`` array from the live routing config so ``/api/docs``
always shows the correct URLs for the current deployment -- no
hardcoded hosts that break across envs.
"""

from __future__ import annotations

import os
import sys as _sys
from pathlib import Path

from media_stack.core.logging_utils import log_swallowed


# Resolve openapi.yaml across deploy modes:
#   1. Source-tree dev (parents[3] = repo root containing contracts/)
#   2. Wheel image, install-root layout (`/opt/media-stack/contracts/`)
#   3. Wheel shared-data layout (`<sys.prefix>/share/media-stack/...`)
#   4. Legacy bind-mount path
# The wheel install moves the file out from under a single hardcoded
# path. Without the candidate list, ``GET /api/openapi.json`` falls
# through to the legacy 50-endpoint stub and the api-docs viewer
# renders empty.
_OPENAPI_PATH_CANDIDATES = (
    Path(__file__).resolve().parents[4] / "contracts" / "api" / "openapi.yaml",
    Path("/opt/media-stack/contracts/api/openapi.yaml"),
    Path(_sys.prefix) / "share" / "media-stack" / "contracts" / "api" / "openapi.yaml",
    Path("/contracts/api/openapi.yaml"),
)


class OpenApiYamlLoader:
    """Resolves + reads ``openapi.yaml`` from the first candidate
    path that exists. Stateful only in the sense that the YAML text
    is cached on the instance after first read."""

    def __init__(self, candidates: tuple[Path, ...] = _OPENAPI_PATH_CANDIDATES) -> None:
        self._candidates = candidates
        self._cached_text: str | None = None
        self._cached_path: Path | None = None

    def resolve_path(self) -> Path:
        for p in self._candidates:
            if p.is_file():
                return p
        return self._candidates[0]  # log a sane default if nothing found

    def load_text(self) -> str:
        if self._cached_text is None:
            path = self.resolve_path()
            try:
                self._cached_text = path.read_text(encoding="utf-8")
                self._cached_path = path
            except Exception:  # noqa: BLE001
                self._cached_text = ""
        return self._cached_text


class ServersBuilder:
    """Builds the OpenAPI ``servers`` list from the live routing config.

    Stateless service. Reads env + routing config inside ``build()``
    so the response reflects the current deployment without a process
    restart.
    """

    def build(self) -> list[dict]:
        from media_stack.api.services import config as config_svc

        servers: list[dict] = [
            {"url": "/", "description": "Current host (auto-detected)"},
        ]
        try:
            routing = config_svc.get_routing()
            gw_host = routing.get("gateway_host", "")
            gw_port = int(routing.get("gateway_port", 80))
            prefix = str(routing.get("app_path_prefix", "/app")).rstrip("/")
            port_str = "" if gw_port == 80 else f":{gw_port}"
            if gw_host:
                # Gateway with path prefix
                # (e.g. http://comp.my/app/media-stack-controller)
                ctrl_name = os.environ.get(
                    "CONTROLLER_CONTAINER_NAME", "media-stack-controller",
                )
                servers.append({
                    "url": f"http://{gw_host}{port_str}{prefix}/{ctrl_name}",
                    "description": f"Gateway ({gw_host}{prefix}/{ctrl_name})",
                })
                # Gateway root (no prefix -- for direct-host routing)
                servers.append({
                    "url": f"http://{gw_host}{port_str}",
                    "description": f"Gateway root ({gw_host})",
                })
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
        ctrl_port = int(os.environ.get("CONTROLLER_PORT", "9100"))
        servers.append({
            "url": f"http://localhost:{ctrl_port}",
            "description": "Localhost direct",
        })
        runtime = os.environ.get("MEDIA_STACK_RUNTIME", "compose")
        if runtime == "kubernetes":
            servers.append({
                "url": f"http://media-stack-controller.media-stack.svc:{ctrl_port}",
                "description": "Kubernetes in-cluster",
            })
        return servers


_OPENAPI_LOADER = OpenApiYamlLoader()
_SERVERS_BUILDER = ServersBuilder()
OPENAPI_YAML = _OPENAPI_LOADER.load_text()

# Backwards-compat aliases for callers that previously imported the
# legacy free symbols from ``handlers_get``. Both underscore-prefixed
# names dispatch through bound methods so any future test can patch
# them via ``mock.patch.object`` and intercept call sites cleanly.
_OPENAPI_YAML = OPENAPI_YAML
_resolve_openapi_yaml = _OPENAPI_LOADER.resolve_path
_build_openapi_servers = _SERVERS_BUILDER.build

__all__ = [
    "OPENAPI_YAML",
    "_OPENAPI_YAML",
    "OpenApiYamlLoader",
    "ServersBuilder",
    "_build_openapi_servers",
    "_resolve_openapi_yaml",
]
