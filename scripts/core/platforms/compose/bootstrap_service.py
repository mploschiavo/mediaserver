"""Compose-target bootstrap execution via bootstrap-runner container."""

from __future__ import annotations

import importlib
import inspect
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from bootstrap_services.top_level_config_model import TopLevelBootstrapConfig

from core.platforms.compose.docker_client import DockerClient

InfoFn = Callable[[str], None]


def _parse_wait_seconds(value: str, *, default_seconds: int = 600) -> int:
    token = str(value or "").strip().lower()
    if not token:
        return default_seconds
    unit = token[-1:] if token else ""
    magnitude_raw = token[:-1] if unit in {"s", "m", "h"} else token
    try:
        magnitude = float(magnitude_raw)
    except Exception:
        return default_seconds
    multiplier = 1.0
    if unit == "m":
        multiplier = 60.0
    elif unit == "h":
        multiplier = 3600.0
    return max(1, int(magnitude * multiplier))


def _decode_logs(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def _normalize_port(value: object) -> str:
    token = str(value or "").strip()
    if token.startswith(":"):
        token = token[1:]
    if not token or not token.isdigit():
        return ""
    port = int(token)
    if port < 1 or port > 65535:
        return ""
    return str(port)


@dataclass(frozen=True)
class ComposeBootstrapConfig:
    namespace: str
    compose_file: Path
    compose_env_file: Path | None
    compose_project_name: str
    bootstrap_runner_image: str
    bootstrap_config_file: Path
    wait_timeout: str
    purpose: str
    preconfigure_api_keys: bool
    apply_initial_preferences: bool
    auto_download_content: bool
    runtime_config_policy_handler: str = ""
    runtime_config_policy_params: dict[str, object] = field(default_factory=dict)
    passthrough_env_vars: tuple[str, ...] = field(default_factory=tuple)
    preflight_handler_specs: tuple[str, ...] = field(default_factory=tuple)
    runtime_artifacts_dir: Path | None = None


@dataclass
class ComposeBootstrapService:
    cfg: ComposeBootstrapConfig
    info: InfoFn
    docker: DockerClient

    def _project_name(self) -> str:
        project = str(self.cfg.compose_project_name or "").strip()
        return project or str(self.cfg.namespace or "").strip() or "media-stack"

    @staticmethod
    def _import_hook(spec: str) -> Callable[..., object]:
        if ":" not in spec:
            raise RuntimeError(f"Invalid compose runtime policy hook spec '{spec}'")
        module_name, symbol_name = spec.split(":", 1)
        module = importlib.import_module(module_name)
        hook = getattr(module, symbol_name, None)
        if not callable(hook):
            raise RuntimeError(
                f"Compose runtime policy hook '{spec}' did not resolve to a callable"
            )
        return hook

    @staticmethod
    def _invoke_hook(
        hook: Callable[..., object],
        *,
        hook_name: str,
        context: dict[str, object],
    ) -> object:
        signature = inspect.signature(hook)
        accepts_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
        )
        if accepts_kwargs:
            return hook(**context)

        accepted = {key: value for key, value in context.items() if key in signature.parameters}
        required_missing = [
            name
            for name, param in signature.parameters.items()
            if param.default is inspect.Parameter.empty
            and param.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
            and name not in accepted
        ]
        if required_missing:
            raise RuntimeError(
                f"Compose runtime policy hook '{hook_name}' requires unsupported "
                f"parameters: {', '.join(required_missing)}"
            )
        return hook(**accepted)

    def _run_preflight_handlers(
        self,
        *,
        compose_env: dict[str, str],
        config_root: Path,
        project_name: str,
    ) -> dict[str, str]:
        specs = tuple(self.cfg.preflight_handler_specs or ())
        if not specs:
            return {}
        env_updates: dict[str, str] = {}
        for spec in specs:
            hook_spec = str(spec or "").strip()
            if not hook_spec:
                continue
            hook = self._import_hook(hook_spec)
            context: dict[str, object] = {
                "compose_env": compose_env,
                "compose_env_file": self.cfg.compose_env_file,
                "compose_file": self.cfg.compose_file,
                "config_root": config_root,
                "project_name": project_name,
                "namespace": self.cfg.namespace,
                "docker": self.docker,
                "info": self.info,
            }
            result = self._invoke_hook(
                hook,
                hook_name=hook_spec,
                context=context,
            )
            if not isinstance(result, dict):
                continue
            for key, value in result.items():
                env_key = str(key or "").strip()
                env_value = str(value or "").strip()
                if not env_key or not env_value:
                    continue
                compose_env[env_key] = env_value
                env_updates[env_key] = env_value
        return env_updates

    def _read_compose_env(self) -> dict[str, str]:
        out = dict(os.environ)
        env_file = self.cfg.compose_env_file
        if env_file is None or not env_file.exists():
            return out
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[str(key).strip()] = str(value).strip()
        return out

    def _resolve_config_root(self, compose_env: dict[str, str]) -> Path:
        token = str(
            compose_env.get("COMPOSE_CONFIG_ROOT") or compose_env.get("CONFIG_ROOT") or ""
        ).strip()
        if token:
            return Path(token).expanduser()
        return Path("/srv/media-stack/config")

    def _resolve_stack_root(self, compose_env: dict[str, str]) -> Path | None:
        explicit = str(compose_env.get("STACK_ROOT") or "").strip()
        if explicit:
            return Path(explicit).expanduser()

        media_root = str(compose_env.get("MEDIA_ROOT") or "").strip()
        data_root = str(compose_env.get("DATA_ROOT") or "").strip()
        if not media_root or not data_root:
            return None

        media_path = Path(media_root).expanduser()
        data_path = Path(data_root).expanduser()
        try:
            common = Path(os.path.commonpath([str(media_path), str(data_path)]))
        except Exception:
            return None
        if not str(common).strip() or str(common) == "/":
            return None
        if media_path == common / "media" and data_path == common / "data":
            return common
        return None

    def _prepare_runtime_config(self, *, compose_env: dict[str, str]) -> Path:
        payload = json.loads(self.cfg.bootstrap_config_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Expected object in compose bootstrap config: {self.cfg.bootstrap_config_file}"
            )

        cfg = TopLevelBootstrapConfig.from_dict(payload).to_dict()
        hook_spec = str(self.cfg.runtime_config_policy_handler or "").strip()
        if not hook_spec:
            raise RuntimeError(
                "Compose runtime policy handler is required. "
                "Set adapter_hooks.bootstrap_job.runtime_config_policy_handler "
                "in bootstrap config."
            )
        hook = self._import_hook(hook_spec)
        context: dict[str, object] = {"cfg": cfg}
        context.update(dict(self.cfg.runtime_config_policy_params or {}))
        if "app_gateway_port" not in context:
            inferred_gateway_port = _normalize_port(
                compose_env.get("APP_GATEWAY_PORT")
                or compose_env.get("TRAEFIK_HTTP_PORT")
                or compose_env.get("EDGE_HTTP_PORT")
                or ""
            )
            if inferred_gateway_port:
                context["app_gateway_port"] = inferred_gateway_port
        self._invoke_hook(
            hook,
            hook_name="apply_runtime_config_policy",
            context=context,
        )

        artifacts_dir = self.cfg.runtime_artifacts_dir
        if artifacts_dir is not None:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            artifact_file = artifacts_dir / "resolved" / "bootstrap.runtime.config.json"
            artifact_file.parent.mkdir(parents=True, exist_ok=True)
            artifact_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
            self.info(f"Compose bootstrap runtime config artifact: {artifact_file}")

        handle = tempfile.NamedTemporaryFile(
            mode="w",
            prefix="compose-bootstrap-config.",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        )
        try:
            handle.write(json.dumps(cfg, indent=2))
            handle.write("\n")
            handle.close()
            return Path(handle.name)
        except Exception:
            handle.close()
            try:
                Path(handle.name).unlink()
            except Exception:
                pass
            raise

    @staticmethod
    def _image_pull_policy() -> str:
        policy = (
            str(
                os.environ.get("COMPOSE_BOOTSTRAP_IMAGE_PULL_POLICY")
                or os.environ.get("BOOTSTRAP_IMAGE_PULL_POLICY")
                or "if-missing"
            )
            .strip()
            .lower()
        )
        if policy not in {"always", "if-missing", "never"}:
            return "if-missing"
        return policy

    def _prepare_bootstrap_runner_image(self) -> None:
        image = str(self.cfg.bootstrap_runner_image or "").strip()
        if not image:
            raise RuntimeError("Compose bootstrap-runner image cannot be empty.")

        pull_policy = self._image_pull_policy()
        has_local = self.docker.image_exists(image)
        self.info(
            "Compose bootstrap-runner image policy: "
            f"policy={pull_policy}, image={image}, local_present={int(has_local)}"
        )

        if pull_policy == "never":
            if not has_local:
                raise RuntimeError(
                    "Compose bootstrap image pull policy is 'never' but image is missing locally: "
                    f"{image}"
                )
            return

        if pull_policy == "if-missing" and has_local:
            self.info(f"Compose bootstrap: using local bootstrap-runner image '{image}'.")
            return

        try:
            self.docker.pull_image(image)
            self.info(f"Compose bootstrap: pulled bootstrap-runner image '{image}'.")
        except Exception:
            if self.docker.image_exists(image):
                self.info("Compose bootstrap: pull failed; using local image " f"'{image}'.")
            else:
                raise

    def _container_logs(self, container: Any) -> str:
        try:
            raw = container.logs(stdout=True, stderr=True, tail=600)
        except Exception:
            return ""
        return _decode_logs(raw)

    def _run_api_mode(
        self,
        *,
        compose_env: dict[str, str],
        config_root: Path,
        runtime_cfg_file: Path,
        project_name: str,
        wait_seconds: int,
    ) -> None:
        """Run bootstrap via the API server (--serve mode).

        Creates a bootstrap-runner container with --serve --auto-run flags,
        then polls the /status API endpoint instead of container state.
        Preflights run inside the container, not on the host.
        """
        network_name = f"{project_name}_default"
        container_name = f"{project_name}-bootstrap-runner"
        api_port = 9100
        bootstrap_env: dict[str, str] = {
            "FULLY_PRECONFIGURED": "1" if self.cfg.apply_initial_preferences else "0",
            "PRECONFIGURE_API_KEYS": "1" if self.cfg.preconfigure_api_keys else "0",
            "APPLY_INITIAL_PREFERENCES": "1" if self.cfg.apply_initial_preferences else "0",
            "AUTO_DOWNLOAD_CONTENT": "1" if self.cfg.auto_download_content else "0",
            "MEDIA_STACK_ENV": str(self.cfg.purpose or "dev"),
            "BOOTSTRAP_API_PORT": str(api_port),
            "BOOTSTRAP_RUN_PREFLIGHTS": "1",
        }
        for env_name in self.cfg.passthrough_env_vars:
            key = str(env_name or "").strip()
            if not key:
                continue
            token = str(compose_env.get(key, "")).strip()
            if token:
                bootstrap_env[key] = token
        volumes: dict[str, dict[str, str]] = {
            str(runtime_cfg_file): {"bind": "/bootstrap/config.json", "mode": "ro"},
            str(config_root): {"bind": "/srv-config", "mode": "rw"},
        }
        stack_root = self._resolve_stack_root(compose_env)
        if stack_root is not None:
            volumes[str(stack_root)] = {"bind": "/srv-stack", "mode": "rw"}
            bootstrap_env.setdefault("DISK_GUARDRAILS_MONITOR_PATH", "/srv-stack")
        # Mount Docker socket for preflight container operations (restart, logs).
        docker_sock = "/var/run/docker.sock"
        if os.path.exists(docker_sock):
            volumes[docker_sock] = {"bind": docker_sock, "mode": "ro"}

        self.docker.ping()
        self.docker.ensure_network(network_name)
        self._prepare_bootstrap_runner_image()
        self.docker.remove_container(container_name, force=True)

        try:
            self.docker.create_container(
                image=self.cfg.bootstrap_runner_image,
                name=container_name,
                detach=True,
                network=network_name,
                volumes=volumes,
                environment=bootstrap_env,
                labels={
                    "com.media-stack.operation": "compose-bootstrap-api",
                    "com.docker.compose.project": project_name,
                },
                command=[
                    "python3",
                    "/opt/media-stack/scripts/bootstrap-apps.py",
                    "--serve",
                    "--auto-run",
                    "--config",
                    "/bootstrap/config.json",
                    "--config-root",
                    "/srv-config",
                    "--wait-timeout",
                    str(wait_seconds),
                    "--env",
                    str(self.cfg.purpose or "dev"),
                ],
            )
            self.docker.start_container(container_name)

            # Resolve container IP for API polling.
            api_base = self._resolve_container_api_base(container_name, api_port)
            self._poll_api_status(api_base, container_name, wait_seconds)
        finally:
            self.docker.remove_container(container_name, force=True)
            try:
                runtime_cfg_file.unlink()
            except Exception:
                pass

    def _resolve_container_api_base(
        self, container_name: str, port: int
    ) -> str:
        """Get the bootstrap-runner container's network IP for API polling."""
        import time as _time

        deadline = _time.time() + 30
        while _time.time() < deadline:
            container = self.docker.get_container(container_name)
            if container is not None:
                try:
                    container.reload()
                    networks = (
                        container.attrs.get("NetworkSettings", {}).get("Networks", {})
                    )
                    for net_info in networks.values():
                        ip = str(net_info.get("IPAddress", "")).strip()
                        if ip:
                            return f"http://{ip}:{port}"
                except Exception:
                    pass
            _time.sleep(1)
        return f"http://{container_name}:{port}"

    def _poll_api_status(
        self, api_base: str, container_name: str, timeout: int
    ) -> None:
        """Poll the bootstrap runner's /status API until complete or error."""
        from urllib import error as _error
        from urllib import request as _request

        deadline = time.time() + timeout
        self.info(f"Compose bootstrap: polling API at {api_base}/status")
        while time.time() < deadline:
            try:
                req = _request.Request(f"{api_base}/status", method="GET")
                with _request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                phase = str(data.get("phase", "")).strip()
                if phase == "complete":
                    self.info("Compose bootstrap completed successfully.")
                    return
                if phase == "error":
                    error_msg = data.get("error", "unknown error")
                    raise RuntimeError(
                        f"Compose bootstrap failed: {error_msg}"
                    )
            except _error.URLError:
                pass  # Container not ready yet.
            except RuntimeError:
                raise
            except Exception:
                pass
            time.sleep(3)

        # Timeout — try to get status one more time.
        container = self.docker.get_container(container_name)
        logs = self._container_logs(container) if container is not None else ""
        raise RuntimeError(
            f"Compose bootstrap API timed out (timeout={timeout}s).\n{logs.strip()}"
        )

    def run(self) -> None:
        compose_env = self._read_compose_env()
        config_root = self._resolve_config_root(compose_env)
        if not config_root.exists():
            raise RuntimeError(
                f"Compose config root does not exist: {config_root}. "
                "Set CONFIG_ROOT in compose env and ensure volume paths exist before bootstrap."
            )
        stack_root = self._resolve_stack_root(compose_env)
        if stack_root is not None:
            stack_root.mkdir(parents=True, exist_ok=True)

        runtime_cfg_file = self._prepare_runtime_config(compose_env=compose_env)
        project_name = self._project_name()

        # API mode: use --serve + poll /status. Preflights run inside the container.
        use_api_mode = os.environ.get("BOOTSTRAP_API_MODE", "0") == "1"
        if use_api_mode:
            wait_seconds = _parse_wait_seconds(self.cfg.wait_timeout, default_seconds=600)
            self._run_api_mode(
                compose_env=compose_env,
                config_root=config_root,
                runtime_cfg_file=runtime_cfg_file,
                project_name=project_name,
                wait_seconds=wait_seconds,
            )
            return

        # Legacy mode: host-side preflights + one-shot container.
        preflight_env_updates = self._run_preflight_handlers(
            compose_env=compose_env,
            config_root=config_root,
            project_name=project_name,
        )
        network_name = f"{project_name}_default"
        container_name = f"{project_name}-bootstrap-runner"
        wait_seconds = _parse_wait_seconds(self.cfg.wait_timeout, default_seconds=600)
        bootstrap_env: dict[str, str] = {
            "FULLY_PRECONFIGURED": "1" if self.cfg.apply_initial_preferences else "0",
            "PRECONFIGURE_API_KEYS": "1" if self.cfg.preconfigure_api_keys else "0",
            "APPLY_INITIAL_PREFERENCES": "1" if self.cfg.apply_initial_preferences else "0",
            "AUTO_DOWNLOAD_CONTENT": "1" if self.cfg.auto_download_content else "0",
            "MEDIA_STACK_ENV": str(self.cfg.purpose or "dev"),
        }
        for env_name in self.cfg.passthrough_env_vars:
            key = str(env_name or "").strip()
            if not key:
                continue
            token = str(compose_env.get(key, "")).strip()
            if token:
                bootstrap_env[key] = token
        for key, value in preflight_env_updates.items():
            bootstrap_env[str(key)] = str(value)
        volumes: dict[str, dict[str, str]] = {
            str(runtime_cfg_file): {"bind": "/bootstrap/config.json", "mode": "ro"},
            str(config_root): {"bind": "/srv-config", "mode": "rw"},
        }
        if stack_root is not None:
            volumes[str(stack_root)] = {"bind": "/srv-stack", "mode": "rw"}
            bootstrap_env.setdefault("DISK_GUARDRAILS_MONITOR_PATH", "/srv-stack")

        self.info(
            "Compose bootstrap: running bootstrap-apps via bootstrap-runner container "
            f"(project={project_name}, network={network_name})."
        )
        self.docker.ping()
        self.docker.ensure_network(network_name)
        self._prepare_bootstrap_runner_image()
        self.docker.remove_container(container_name, force=True)

        try:
            self.docker.create_container(
                image=self.cfg.bootstrap_runner_image,
                name=container_name,
                detach=True,
                network=network_name,
                volumes=volumes,
                environment=bootstrap_env,
                labels={
                    "com.media-stack.operation": "compose-bootstrap",
                    "com.docker.compose.project": project_name,
                },
                command=[
                    "python3",
                    "/opt/media-stack/scripts/bootstrap-apps.py",
                    "--config",
                    "/bootstrap/config.json",
                    "--config-root",
                    "/srv-config",
                    "--wait-timeout",
                    str(wait_seconds),
                    "--env",
                    str(self.cfg.purpose or "dev"),
                ],
            )
            self.docker.start_container(container_name)
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                state = self.docker.container_state(container_name)
                if state is None:
                    raise RuntimeError(f"Compose bootstrap container disappeared: {container_name}")
                if state.status == "exited":
                    if (state.exit_code or 0) == 0:
                        self.info("Compose bootstrap completed successfully.")
                        return
                    logs = self._container_logs(self.docker.get_container(container_name))
                    raise RuntimeError(
                        "Compose bootstrap failed "
                        f"(container={container_name}, exit_code={state.exit_code}).\n"
                        f"{logs.strip()}"
                    )
                if state.status in {"dead"}:
                    logs = self._container_logs(self.docker.get_container(container_name))
                    raise RuntimeError(
                        f"Compose bootstrap container entered status '{state.status}'.\n{logs.strip()}"
                    )
                time.sleep(2)
            container = self.docker.get_container(container_name)
            if container is not None:
                try:
                    container.stop(timeout=10)
                except Exception:
                    pass
            logs = self._container_logs(container) if container is not None else ""
            raise RuntimeError(
                "Compose bootstrap timed out "
                f"(container={container_name}, timeout={wait_seconds}s).\n{logs.strip()}"
            )
        finally:
            self.docker.remove_container(container_name, force=True)
            try:
                runtime_cfg_file.unlink()
            except Exception:
                pass
