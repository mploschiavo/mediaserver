"""Auto-discover the real CONFIG_ROOT and extract API keys from containers.

The controller mounts CONFIG_ROOT from a host path, but the arr apps may
have their config files on a different host path.  When the paths don't
match, the controller can't read API keys from config files and spins
for 180s per service.

This module runs a fast preflight (< 5 s) that tries four strategies:

1. Docker container mount inspection  -- find what host path maps to /config
2. Docker container environment vars  -- look for injected API keys
3. HTTP API key extraction             -- (handled by existing read_api_key_via_http)
4. Candidate config root path scan    -- scan common paths for config files
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_stack.api.services.registry import SERVICES

logger = logging.getLogger(__name__)

# Common container-internal config mount targets used by arr-stack images.
_CONTAINER_CONFIG_TARGETS = ("/config", "/data")

# Candidate host paths to scan when Docker is unavailable.
_CANDIDATE_ROOTS = (
    "/srv-config",
    "/config",
    "/opt/config",
)


@dataclass
class DiscoveryResult:
    """Outcome of config-root auto-discovery."""

    config_root: str | None = None
    keys: dict[str, str] = field(default_factory=dict)
    source: str = ""


# ------------------------------------------------------------------
# Shared: container name -> service ID mapping
# ------------------------------------------------------------------

def _build_container_map(containers: list[Any]) -> dict[str, Any]:
    """Build a mapping of service name (lowercase) -> container object.

    Handles common Compose naming patterns:
      - Exact name (e.g. "sonarr")
      - Compose v2 dashes: "project-service-N" -> "service"
      - Compose v1 underscores: "project_service_N" -> "service"
      - com.docker.compose.service label (authoritative)
    """
    container_map: dict[str, Any] = {}
    for c in containers:
        container_map[c.name.lower()] = c

        # Best: use the compose service label (authoritative)
        try:
            labels = (c.attrs or {}).get("Config", {}).get("Labels", {}) or {}
            compose_svc = labels.get("com.docker.compose.service", "").strip()
            if compose_svc:
                container_map[compose_svc.lower()] = c
        except Exception as exc:
            log_swallowed(exc)

        # Compose v2 dashes: "project-service-N" -> strip trailing -N
        parts = c.name.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            inner = parts[0]
            svc_seg = inner.rsplit("-", 1)[-1] if "-" in inner else inner
            container_map[svc_seg.lower()] = c
        elif len(parts) == 2:
            container_map[parts[1].lower()] = c

        # Compose v1 underscores: "project_service_1"
        uparts = c.name.rsplit("_", 1)
        if len(uparts) >= 2 and uparts[-1].isdigit():
            inner = uparts[0]
            suffix = inner.rsplit("_", 1)[-1] if "_" in inner else inner
            container_map[suffix.lower()] = c

    return container_map


# ------------------------------------------------------------------
# Method 1: Docker container mount inspection
# ------------------------------------------------------------------

def _discover_via_docker_mounts(log: Any = None) -> DiscoveryResult:
    """Inspect running container mounts to derive the real CONFIG_ROOT.

    For each registry service, find its container and look at what host
    path is bound to /config.  If sonarr's /config maps to
    /tmp/compose-test/config/sonarr, then CONFIG_ROOT is
    /tmp/compose-test/config.
    """
    result = DiscoveryResult(source="docker_mounts")
    try:
        import docker  # type: ignore
        client = docker.from_env()
        containers = client.containers.list()
    except Exception as exc:
        if log:
            log(f"[DEBUG] Docker mount inspection skipped: {exc}")
        return result

    container_map = _build_container_map(containers)

    candidate_roots: dict[str, int] = {}  # path -> vote count

    for svc in SERVICES:
        if not svc.api_key_config:
            continue

        # Find the container for this service
        container = container_map.get(svc.id.lower()) or container_map.get(svc.host.lower())
        if not container:
            continue

        try:
            attrs = container.attrs or {}
            mounts = attrs.get("Mounts") or []
            for mount in mounts:
                dest = (mount.get("Destination") or "").rstrip("/")
                source = (mount.get("Source") or "").rstrip("/")
                if not dest or not source:
                    continue
                if dest in _CONTAINER_CONFIG_TARGETS:
                    # The host path maps to /config inside the container.
                    # If source = /tmp/test/config/sonarr, and api_key_config
                    # = sonarr/config.xml, then the config root is the parent
                    # of the service subdirectory.
                    config_subdir = svc.api_key_config.split("/")[0]
                    if source.lower().endswith("/" + config_subdir.lower()):
                        # source = /host/path/sonarr -> root = /host/path
                        root = source[: -(len(config_subdir) + 1)]
                    else:
                        # source IS the config root (mount is at service level)
                        root = source
                    if root:
                        candidate_roots[root] = candidate_roots.get(root, 0) + 1
        except Exception as exc:
            log_swallowed(exc)
            continue

    if candidate_roots:
        # Pick the root with the most votes (most services agree)
        best_root = max(candidate_roots, key=candidate_roots.get)  # type: ignore[arg-type]
        if Path(best_root).is_dir():
            result.config_root = best_root

    return result


# ------------------------------------------------------------------
# Method 2: Docker container environment inspection
# ------------------------------------------------------------------

def _discover_via_docker_env(log: Any = None) -> DiscoveryResult:
    """Inspect running containers for API keys in their environment.

    Arr apps support injected env vars like SONARR__AUTH__APIKEY=xxx.
    Also check for the standard env var names (e.g. SONARR_API_KEY).
    """
    result = DiscoveryResult(source="docker_env")
    try:
        import docker  # type: ignore
        client = docker.from_env()
        containers = client.containers.list()
    except Exception as exc:
        if log:
            log(f"[DEBUG] Docker env inspection skipped: {exc}")
        return result

    # Build a mapping of service id -> expected env var names
    svc_env_map: dict[str, list[str]] = {}
    for svc in SERVICES:
        if not svc.api_key_env:
            continue
        # Standard env var (e.g. SONARR_API_KEY)
        candidates = [svc.api_key_env]
        # Arr double-underscore pattern (e.g. SONARR__AUTH__APIKEY)
        prefix = svc.id.upper()
        candidates.append(f"{prefix}__AUTH__APIKEY")
        svc_env_map[svc.id] = candidates

    container_map = _build_container_map(containers)

    for svc in SERVICES:
        if svc.id not in svc_env_map:
            continue
        container = container_map.get(svc.id.lower()) or container_map.get(svc.host.lower())
        if not container:
            continue
        try:
            attrs = container.attrs or {}
            config = attrs.get("Config") or {}
            env_list = config.get("Env") or []
            env_dict: dict[str, str] = {}
            for entry in env_list:
                if "=" in entry:
                    k, v = entry.split("=", 1)
                    env_dict[k] = v

            for candidate_var in svc_env_map[svc.id]:
                val = env_dict.get(candidate_var, "").strip()
                if val:
                    result.keys[svc.api_key_env] = val
                    break
        except Exception as exc:
            log_swallowed(exc)
            continue

    return result


# ------------------------------------------------------------------
# Method 4: Candidate config root scanning
# ------------------------------------------------------------------

def _discover_via_path_scan(
    current_root: str,
    log: Any = None,
) -> DiscoveryResult:
    """Scan common host paths for config files to find the real CONFIG_ROOT.

    Checks the configured root first, then well-known fallback paths.
    Picks the first path that contains actual service config files.
    """
    result = DiscoveryResult(source="path_scan")

    # Services that have config files we can look for
    probe_configs = [
        (svc.api_key_config, svc.api_key_env)
        for svc in SERVICES
        if svc.api_key_config and svc.api_key_format == "xml"
    ]
    if not probe_configs:
        return result

    # Build candidate list: current root first, then well-known paths
    candidates = [current_root]
    for p in _CANDIDATE_ROOTS:
        if p != current_root:
            candidates.append(p)

    for candidate in candidates:
        root = Path(candidate)
        if not root.is_dir():
            continue
        found = 0
        for config_rel, _env_key in probe_configs:
            if (root / config_rel).is_file():
                found += 1
        if found > 0:
            result.config_root = str(root)
            if log:
                log(
                    f"[DEBUG] Path scan found {found} config file(s) "
                    f"at {candidate}"
                )
            break

    return result


# ------------------------------------------------------------------
# Main orchestrator
# ------------------------------------------------------------------

def discover_config_root(
    current_root: str | None = None,
    log: Any = None,
) -> DiscoveryResult:
    """Run all discovery methods and return the best result.

    Returns a DiscoveryResult with the resolved config_root (or None if
    unchanged) and any API keys extracted from container environments.

    The method chain is ordered by reliability:
    1. Docker mount inspection  (most authoritative)
    2. Docker env inspection    (captures injected keys)
    3. Path scanning            (filesystem heuristic)
    """
    if current_root is None:
        current_root = os.environ.get("CONFIG_ROOT", "/srv-config")

    merged = DiscoveryResult()
    merged_keys: dict[str, str] = {}

    # --- Method 1: Docker mount inspection ---
    try:
        mount_result = _discover_via_docker_mounts(log=log)
        if mount_result.config_root:
            merged.config_root = mount_result.config_root
            merged.source = "docker_mounts"
            if log:
                log(
                    f"[INFO] Config root discovered via Docker mounts: "
                    f"{mount_result.config_root}"
                )
        merged_keys.update(mount_result.keys)
    except Exception as exc:
        if log:
            log(f"[WARN] Docker mount discovery failed: {exc}")

    # --- Method 2: Docker env inspection ---
    try:
        env_result = _discover_via_docker_env(log=log)
        merged_keys.update(env_result.keys)
        if env_result.keys and log:
            log(
                f"[INFO] Discovered {len(env_result.keys)} API key(s) "
                f"from container environment variables"
            )
    except Exception as exc:
        if log:
            log(f"[WARN] Docker env discovery failed: {exc}")

    # --- Method 3 (HTTP) is handled by existing read_api_key_via_http ---

    # --- Method 4: Path scanning (fallback when Docker is unavailable) ---
    if not merged.config_root:
        try:
            scan_result = _discover_via_path_scan(current_root, log=log)
            if scan_result.config_root and scan_result.config_root != current_root:
                merged.config_root = scan_result.config_root
                merged.source = "path_scan"
                if log:
                    log(
                        f"[INFO] Config root discovered via path scan: "
                        f"{scan_result.config_root}"
                    )
        except Exception as exc:
            if log:
                log(f"[WARN] Path scan discovery failed: {exc}")

    merged.keys = merged_keys
    return merged
