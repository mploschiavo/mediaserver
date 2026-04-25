"""System diagnostics, backup/restore, env vars, manifests, onboarding, drift."""
from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import json
import os
import time
from pathlib import Path
from typing import Any

from .. import _resolve as _resolve_mod
from ._profile import ProfileService
# hoisted from per-method import to reduce CIRCULAR_IMPORT_RISK_RATCHET drift
# (registry is a leaf data module — no cycle with _diagnostics)
from ..registry import SERVICES as _REGISTRY_SERVICES
import logging


class DiagnosticsService:
    """System environment, backup/restore, manifests, onboarding, config drift."""

    def __init__(self, profile: ProfileService):
        self._profile = profile

    def get_env(self) -> dict[str, Any]:
        import platform
        import socket
        namespace = os.environ.get("K8S_NAMESPACE", "")
        profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
        profile_name = ""
        resolved = _resolve_mod.resolve_profile_path(profile_file)
        if resolved:
            profile_name = Path(resolved).name
        node_ip = os.environ.get("NODE_IP", "")
        if not node_ip:
            try:
                node_ip = socket.gethostbyname(socket.gethostname())
            except Exception:
                node_ip = ""
        node_ips: list[str] = [node_ip] if node_ip else []
        if namespace:
            try:
                from kubernetes import client as k8s_client, config as k8s_config
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                v1 = k8s_client.CoreV1Api()
                nodes = v1.list_node()
                node_ips = []
                for node in nodes.items:
                    for addr in (node.status.addresses or []):
                        if addr.type == "InternalIP":
                            node_ips.append(addr.address)
                            break
            except Exception as exc:
                log_swallowed(exc)
        return {
            "namespace": namespace, "profile_name": profile_name,
            "node_ip": node_ip, "node_ips": node_ips, "node_count": len(node_ips),
            "platform": platform.platform(), "python": platform.python_version(),
            "runtime": "kubernetes" if namespace else "compose",
        }

    def get_backup(self, state: Any) -> bytes:
        backup: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "version": "2",
            "env": self.get_env(),
            "state": state.to_dict() if hasattr(state, "to_dict") else {},
        }
        resolved_profile = _resolve_mod.resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
        if resolved_profile:
            backup["profile_raw"] = Path(resolved_profile).read_text(encoding="utf-8", errors="replace")
        _backup_svcs = _REGISTRY_SERVICES
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        service_configs: dict[str, str] = {}
        if config_root.is_dir():
            config_files: list[str] = []
            for svc in _backup_svcs:
                if svc.api_key_config:
                    config_files.append(svc.api_key_config)
                if svc.password_config:
                    config_files.append(svc.password_config)
            seen: set[str] = set()
            for cf in config_files:
                if cf not in seen:
                    seen.add(cf)
                    full_path = config_root / cf
                    if full_path.is_file():
                        try:
                            content = full_path.read_text(encoding="utf-8", errors="replace")
                            if len(content) < 100_000:
                                service_configs[cf] = content
                        except Exception as exc:
                            log_swallowed(exc)
        if service_configs:
            backup["service_configs"] = service_configs
        api_keys: dict[str, str] = {}
        api_keys_masked: dict[str, str] = {}
        for key, value in sorted(os.environ.items()):
            if key.endswith("_API_KEY") and value:
                api_keys[key] = value
                api_keys_masked[key] = value[:8] + "..." if len(value) > 8 else value
        if api_keys:
            backup["api_keys"] = api_keys
            backup["api_keys_masked"] = api_keys_masked
        valid_paths: list[str] = []
        for svc in _backup_svcs:
            if svc.api_key_config:
                valid_paths.append(svc.api_key_config)
            if svc.password_config:
                valid_paths.append(svc.password_config)
        backup["valid_config_paths"] = sorted(set(valid_paths))
        return json.dumps(backup, indent=2, default=str).encode("utf-8")

    def restore_backup(self, backup: dict[str, Any], state: Any = None) -> dict[str, Any]:
        version = str(backup.get("version", ""))
        if version not in ("1", "2"):
            return {"status": "error", "error": f"unsupported backup version: {version!r}"}
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        restored: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []
        _restore_svcs = _REGISTRY_SERVICES
        valid_paths: set[str] = set()
        for svc in _restore_svcs:
            if svc.api_key_config:
                valid_paths.add(svc.api_key_config)
            if svc.password_config:
                valid_paths.add(svc.password_config)
        pre_restore: dict[str, str] = {}
        service_configs = backup.get("service_configs", {})
        if not isinstance(service_configs, dict):
            return {"status": "error", "error": "service_configs must be an object"}
        for rel_path in service_configs:
            existing = config_root / rel_path
            if existing.is_file():
                try:
                    pre_restore[rel_path] = existing.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:
                    log_swallowed(exc)
        for rel_path, content in service_configs.items():
            if ".." in rel_path or rel_path.startswith("/"):
                errors.append(f"skipped unsafe path: {rel_path}")
                continue
            if valid_paths and rel_path not in valid_paths:
                skipped.append(rel_path)
                continue
            target = config_root / rel_path
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                restored.append(rel_path)
            except Exception as exc:
                errors.append(f"{rel_path}: {exc}")
        if errors and len(errors) > len(restored):
            rollback_ok = 0
            for rel_path, content in pre_restore.items():
                try:
                    (config_root / rel_path).write_text(content, encoding="utf-8")
                    rollback_ok += 1
                except Exception as exc:
                    log_swallowed(exc)
            return {"status": "rolled_back", "errors": errors, "rollback_count": rollback_ok,
                    "note": "More errors than successes — rolled back to pre-restore state"}
        api_keys = backup.get("api_keys", {})
        keys_restored: list[str] = []
        if isinstance(api_keys, dict):
            for key, value in api_keys.items():
                if key.endswith("_API_KEY") and isinstance(value, str) and value and "..." not in value:
                    os.environ[key] = value
                    keys_restored.append(key)
        return {"status": "ok" if not errors else "partial", "restored": restored,
                "skipped": skipped, "keys_restored": keys_restored, "errors": errors,
                "pre_restore_count": len(pre_restore), "note": "Restart services to apply restored configs"}

    def get_envvars(self) -> dict[str, str]:
        """Return a sanitised view of the controller's env vars.

        Secret values (anything ending in PASSWORD/SECRET/TOKEN/KEY,
        plus a small explicit set of known credential vars) are
        masked rather than included verbatim. Before the
        admin-bootstrap redesign, ``STACK_ADMIN_PASSWORD`` was the
        live admin credential and the dashboard happily echoed it
        here. After the redesign that env var is a one-time seed
        only; surfacing it (a) leaks the seed to anyone with read
        access and (b) misleads the user into thinking the seed is
        still the live password after they've rotated it. Mask it
        unconditionally — UIs that want to know "is a seed set?"
        can check ``password_set`` on ``/api/keys``.
        """
        from ..registry import SERVICES as _env_svcs
        _platform = ("BOOTSTRAP_", "STACK_", "K8S_", "CONTROLLER_", "PUID", "PGID", "TZ")
        _svc = {e.api_key_env.split("_")[0] + "_" for e in _env_svcs if e.api_key_env}
        relevant_prefixes = set(_platform) | _svc
        secret_suffixes = ("PASSWORD", "SECRET", "TOKEN", "KEY", "_API_KEY")
        secret_exact = {
            "STACK_ADMIN_PASSWORD",
            "AUTHELIA_JWT_SECRET",
            "AUTHELIA_SESSION_SECRET",
            "AUTHELIA_STORAGE_ENCRYPTION_KEY",
        }

        def _mask(name: str, value: str) -> str:
            if name in secret_exact or name.endswith(secret_suffixes):
                return "***" if value else ""
            return value

        return {
            k: _mask(k, v)
            for k, v in sorted(os.environ.items())
            if any(k.startswith(p) for p in relevant_prefixes)
        }

    def set_envvar(self, key: str, value: str) -> dict[str, Any]:
        os.environ[key] = value
        return {"status": "set", "key": key, "value": value}

    def delete_envvar(self, key: str) -> dict[str, Any]:
        """Drop an env var from the controller process's environment.

        Symmetric with ``set_envvar`` — both touch only the running
        process. Persistence is the deployment's job (k8s Secret,
        compose .env, etc.); this endpoint exists so the dashboard
        can remove a runtime-injected var without a controller
        restart. The UI EnvVarsEditorCard had Add/Update wired to
        ``set_envvar`` but no Delete affordance — operators had to
        edit the secret + restart, which lost runtime state.

        Returns ``existed: false`` when the key was already absent
        so the dashboard can render an idempotent confirmation
        rather than treating missing-key as an error."""
        existed = key in os.environ
        os.environ.pop(key, None)
        return {"status": "deleted", "key": key, "existed": existed}

    def get_manifests(self) -> dict[str, Any]:
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if namespace:
            try:
                from kubernetes import client as k8s_client, config as k8s_config
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                apps_v1 = k8s_client.AppsV1Api()
                deps = apps_v1.list_namespaced_deployment(namespace)
                services = [{"name": d.metadata.name, "image": d.spec.template.spec.containers[0].image if d.spec.template.spec.containers else ""} for d in deps.items]
                return {"type": "kubernetes", "namespace": namespace, "deployments": len(services), "services": services}
            except Exception as exc:
                return {"type": "kubernetes", "error": str(exc)[:80]}
        compose_file = os.environ.get("COMPOSE_FILE", "")
        if not compose_file:
            for candidate in ["/compose/docker-compose.yml", "./docker-compose.yml"]:
                if Path(candidate).is_file():
                    compose_file = candidate
                    break
        if compose_file and Path(compose_file).is_file():
            return {"type": "compose", "file": compose_file, "content": Path(compose_file).read_text(encoding="utf-8", errors="replace")}
        config_path = _resolve_mod.resolve_config_path()
        if config_path:
            try:
                cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
                summary = {
                    "services": list((cfg.get("services") or {}).keys()) if isinstance(cfg.get("services"), dict) else [],
                    "disk_guardrails": cfg.get("disk_guardrails", {}).get("enabled", False),
                    "preflight_handlers": [h.get("name") for h in cfg.get("container_preflight_handlers", [])],
                    "post_handlers": [h.get("name") for h in cfg.get("container_post_setup_handlers", [])],
                }
                return {"type": "bootstrap-config", "file": config_path, "content": json.dumps(summary, indent=2)}
            except Exception as exc:
                log_swallowed(exc)
        try:
            import docker
            client = docker.from_env()
            containers = [{"name": c.name, "image": c.image.tags[0] if c.image.tags else str(c.image.short_id), "status": c.status} for c in client.containers.list()]
            return {"type": "compose-runtime", "content": json.dumps(containers, indent=2), "note": "Compose file not mounted. Showing running containers."}
        except Exception as exc:
            log_swallowed(exc)
        return {"type": "unknown", "content": None, "error": "No manifest found. Mount compose file or use K8s."}

    def get_onboarding_status(self) -> dict[str, Any]:
        from ..registry import SERVICES
        from ..health import discover_api_keys, probe_services
        from ...cache import api_cache
        # Lazy imports to avoid circular deps
        from ._media_server import LibraryConfigService
        from ._routing import RoutingConfigService
        _libs = LibraryConfigService(self._profile)
        _routing = RoutingConfigService(self._profile)

        steps: list[dict[str, Any]] = []
        health = probe_services(api_cache)
        healthy = health.get("healthy", 0)
        total = health.get("total", 0)
        steps.append({"id": "services_running", "label": "Services running",
                       "status": "ok" if healthy >= total * 0.8 else "warn" if healthy > 0 else "error",
                       "detail": f"{healthy}/{total} healthy"})
        keys = discover_api_keys()
        key_count = len(keys)
        expected = len([s for s in SERVICES if s.api_key_env])
        steps.append({"id": "api_keys", "label": "API keys discovered",
                       "status": "ok" if key_count >= expected else "warn",
                       "detail": f"{key_count}/{expected} keys"})
        libs = _libs.get_libraries()
        lib_count = len(libs.get("libraries", []))
        steps.append({"id": "libraries", "label": "Media libraries configured",
                       "status": "ok" if lib_count > 0 else "pending",
                       "detail": f"{lib_count} libraries" if lib_count else "No libraries — go to Config > Libraries"})
        routing = _routing.get_routing()
        has_routing = routing.get("gateway_host", "") != ""
        steps.append({"id": "routing", "label": "Network routing configured",
                       "status": "ok" if has_routing else "pending",
                       "detail": routing.get("gateway_host", "not set")})
        data, _ = self._profile.load()
        bindings = data.get("technology_bindings", {})
        has_torrent = bool(bindings.get("torrent_client"))
        has_usenet = bool(bindings.get("usenet_client"))
        steps.append({"id": "download_clients", "label": "Download clients configured",
                       "status": "ok" if (has_torrent or has_usenet) else "pending",
                       "detail": ", ".join(filter(None, [bindings.get("torrent_client"), bindings.get("usenet_client")])) or "none configured"})
        steps.append({"id": "bootstrap", "label": "Initial bootstrap completed",
                       "status": "ok" if health.get("healthy", 0) > 0 else "pending",
                       "detail": "Run 'Configure All' to bootstrap the stack"})
        completed = sum(1 for s in steps if s["status"] == "ok")
        return {"steps": steps, "completed": completed, "total": len(steps),
                "progress_pct": round(completed / len(steps) * 100) if steps else 0,
                "is_first_run": completed < len(steps) * 0.5}

    def add_custom_service(self, service_def: dict[str, Any]) -> dict[str, Any]:
        import yaml
        svc_id = str(service_def.get("id", "")).strip().lower()
        if not svc_id or not service_def.get("name") or not service_def.get("port"):
            return {"error": "id, name, and port are required"}
        if not svc_id.replace("-", "").replace("_", "").isalnum():
            return {"error": "id must be alphanumeric (hyphens and underscores allowed)"}
        svc_dir = Path(os.environ.get("SERVICES_REGISTRY_DIR", ""))
        if not svc_dir.is_dir():
            svc_dir = Path(__file__).resolve().parents[5] / "contracts" / "services"
        if not svc_dir.is_dir():
            return {"error": "Services directory not found"}
        target = svc_dir / f"{svc_id}.yaml"
        if target.exists():
            return {"error": f"Service '{svc_id}' already exists"}
        svc_yaml = {"service": {
            "id": svc_id, "name": str(service_def.get("name", svc_id)),
            "desc": str(service_def.get("desc", "")), "category": str(service_def.get("category", "custom")),
            "host": str(service_def.get("host", svc_id)), "port": int(service_def.get("port", 0)),
            "health_path": str(service_def.get("health_path", "/")), "web_ui": bool(service_def.get("web_ui", True)),
        }}
        try:
            with open(target, "w") as f:
                yaml.dump(svc_yaml, f, default_flow_style=False, sort_keys=False)
            from ..registry import reload_registry
            reload_registry()
            return {"status": "created", "file": str(target), "service_id": svc_id}
        except Exception as exc:
            return {"error": str(exc)[:120]}

    def get_config_drift(self) -> dict[str, Any]:
        import logging as _logging
        import yaml
        _drift_log = _logging.getLogger("media_stack")
        drifts: list[dict[str, str]] = []
        # Lazy imports to avoid circular deps
        from ._routing import RoutingConfigService
        from ._media_server import LibraryConfigService
        from ._metadata import MetadataConfigService
        from ._livetv import LiveTvConfigService
        _routing = RoutingConfigService(self._profile)
        _libs = LibraryConfigService(self._profile)
        _meta = MetadataConfigService(self._profile)
        _ltv = LiveTvConfigService(self._profile)

        resolved = _resolve_mod.resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
        profile_routing: dict[str, Any] = {}
        if resolved:
            try:
                with open(resolved) as f:
                    profile = yaml.safe_load(f) or {}
                profile_routing = profile.get("routing") or {}
            except Exception as exc:
                log_swallowed(exc)
        live_routing = _routing.get_routing()
        for key in ("base_domain", "stack_subdomain", "gateway_host", "gateway_port", "strategy"):
            expected = str(profile_routing.get(key, ""))
            actual = str(live_routing.get(key, ""))
            if expected and actual and expected != actual:
                drifts.append({"area": "routing", "key": key, "expected": expected, "actual": actual})
        from ..registry import SERVICES, read_api_key_from_file
        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        for svc in SERVICES:
            if not svc.api_key_env or not svc.api_key_config:
                continue
            env_key = (os.environ.get(svc.api_key_env) or "").strip()
            file_key = read_api_key_from_file(svc.id, config_root)
            if env_key and file_key and env_key != file_key:
                drifts.append({"area": "api_key", "key": svc.id,
                    "expected": f"{env_key[:4]}...{env_key[-4:]}" if len(env_key) > 8 else "set",
                    "actual": f"{file_key[:4]}...{file_key[-4:]}" if len(file_key) > 8 else "set",
                    "note": "Env var differs from config file — run bootstrap to resync"})
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if not namespace:
            try:
                import docker
                client = docker.from_env()
                for c in client.containers.list():
                    image = c.image.tags[0] if c.image.tags else ""
                    if image and "@sha256:" not in image:
                        created = c.image.attrs.get("Created", "") if c.image.attrs else ""
                        started = c.attrs.get("State", {}).get("StartedAt", "")
                        if created and started and created > started:
                            drifts.append({"area": "image", "key": c.name,
                                "expected": "latest pulled image", "actual": f"running image created {created[:19]}",
                                "note": "Container running older image than what's pulled"})
            except Exception as exc:
                log_swallowed(exc)
        try:
            from ..health import probe_credentials
            cred_result = probe_credentials()
            for svc_id, status in cred_result.get("credentials", {}).items():
                if status == "fail":
                    drifts.append({"area": "credentials", "key": svc_id,
                        "expected": "ok (valid login)", "actual": "fail (wrong password)",
                        "note": "Run Validate Credentials to auto-sync"})
        except Exception as exc:
            log_swallowed(exc)
        ltv = _ltv.get_livetv_sources()
        if ltv.get("source") == "not_configured":
            # Profile/app_config don't show tuners — but check the
            # runtime livetv-tuners directory the controller writes
            # to (e.g. /srv-config/livetv-tuners/*.m3u). Jellyfin's
            # configure-livetv job adds tuners to Jellyfin directly
            # AND drops the m3u/xmltv files there, so a populated
            # directory is the source-of-truth signal that Live TV
            # IS configured even when the profile YAML doesn't list
            # tuners explicitly. Without this check, the drift
            # report fires on every clean install where the user
            # added tuners through the dashboard. (v1.0.128.)
            import os as _os
            from pathlib import Path as _P
            cfg_root = _P(_os.environ.get("CONFIG_ROOT", "/srv-config"))
            # Materialized tuner files live under
            # <config_root>/<media-server-id>/livetv-tuners/.
            # Walk all per-app livetv-tuners dirs so the check works
            # whether the media server is jellyfin / plex / emby /
            # mythtv (each gets its own subdir per the registry id).
            has_runtime_tuners = False
            try:
                for sub in cfg_root.iterdir():
                    tuners_dir = sub / "livetv-tuners"
                    if not tuners_dir.is_dir():
                        continue
                    if any(
                        f.suffix.lower() in (".m3u", ".m3u8")
                        for f in tuners_dir.iterdir() if f.is_file()
                    ):
                        has_runtime_tuners = True
                        break
            except (OSError, PermissionError) as exc:
                log_swallowed(exc)
            if not has_runtime_tuners:
                drifts.append({"area": "live_tv", "key": "tuners",
                    "expected": "at least 1 tuner configured", "actual": "none",
                    "note": "Go to Config > Live TV to add IPTV sources"})
        libs = _libs.get_libraries()
        if not libs.get("libraries"):
            drifts.append({"area": "libraries", "key": "media_libraries",
                "expected": "at least 1 library configured", "actual": "none",
                "note": "Go to Config > Libraries to add media folders"})
        meta = _meta.get_metadata_settings()
        if meta.get("source") == "defaults":
            drifts.append({"area": "metadata", "key": "language",
                "expected": "configured", "actual": f"{meta.get('language', '?')}/{meta.get('country', '?')} (default)",
                "note": "Review in Config > Metadata if you need a different language"})
        return {"drifts": drifts, "total": len(drifts), "clean": len(drifts) == 0}
