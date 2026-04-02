"""Docker SDK adapter utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .exceptions import ConfigError, DockerError


def _is_not_found(exc: Exception) -> bool:
    status_code = int(getattr(exc, "status_code", 0) or 0)
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return status_code == 404 or "notfound" in name or "not found" in message


@dataclass(frozen=True)
class DockerContainerState:
    name: str
    status: str
    health: str
    image: str
    exit_code: int | None = None


@dataclass
class DockerClient:
    client: Any

    @classmethod
    def from_environment(cls) -> "DockerClient":
        try:
            import docker  # type: ignore
        except Exception as exc:  # pragma: no cover - import path depends on runtime image
            raise ConfigError(
                "The Docker SDK for Python is required. Install with: pip install docker"
            ) from exc
        try:
            return cls(client=docker.from_env())
        except Exception as exc:
            raise DockerError(f"Could not initialize Docker client: {exc}") from exc

    def ping(self) -> None:
        try:
            self.client.ping()
        except Exception as exc:
            raise DockerError(f"Docker daemon is not reachable: {exc}") from exc

    def pull_image(self, image: str) -> None:
        try:
            self.client.images.pull(image)
        except Exception as exc:
            raise DockerError(f"Failed pulling image '{image}': {exc}") from exc

    def ensure_network(self, name: str, *, driver: str = "bridge") -> None:
        try:
            self.client.networks.get(name)
            return
        except Exception as exc:
            if not _is_not_found(exc):
                raise DockerError(f"Failed reading network '{name}': {exc}") from exc
        try:
            self.client.networks.create(name=name, driver=driver)
        except Exception as exc:
            raise DockerError(f"Failed creating network '{name}': {exc}") from exc

    def remove_network(self, name: str) -> bool:
        try:
            network = self.client.networks.get(name)
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise DockerError(f"Failed reading network '{name}': {exc}") from exc
        try:
            network.remove()
            return True
        except Exception as exc:
            raise DockerError(f"Failed removing network '{name}': {exc}") from exc

    def list_project_containers(
        self, project_name: str, *, include_stopped: bool = True
    ) -> list[Any]:
        try:
            return list(
                self.client.containers.list(
                    all=include_stopped,
                    filters={"label": f"com.docker.compose.project={project_name}"},
                )
            )
        except Exception as exc:
            raise DockerError(
                f"Failed listing containers for compose project '{project_name}': {exc}"
            ) from exc

    def get_container(self, name: str) -> Any | None:
        try:
            return self.client.containers.get(name)
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise DockerError(f"Failed reading container '{name}': {exc}") from exc

    def remove_container(self, name: str, *, force: bool = True) -> bool:
        container = self.get_container(name)
        if container is None:
            return False
        try:
            container.remove(force=force)
            return True
        except Exception as exc:
            raise DockerError(f"Failed removing container '{name}': {exc}") from exc

    def create_container(self, **kwargs: Any) -> Any:
        try:
            return self.client.containers.create(**kwargs)
        except Exception as exc:
            image = str(kwargs.get("image") or "")
            name = str(kwargs.get("name") or "")
            raise DockerError(
                f"Failed creating container '{name}' (image='{image}'): {exc}"
            ) from exc

    def start_container(self, name: str) -> None:
        container = self.get_container(name)
        if container is None:
            raise DockerError(f"Container '{name}' does not exist.")
        try:
            container.start()
        except Exception as exc:
            raise DockerError(f"Failed starting container '{name}': {exc}") from exc

    def container_state(self, name: str) -> DockerContainerState | None:
        container = self.get_container(name)
        if container is None:
            return None
        try:
            container.reload()
        except Exception:
            # Best effort reload. Continue with last known attrs.
            pass
        attrs = dict(getattr(container, "attrs", {}) or {})
        state = dict(attrs.get("State") or {})
        image_cfg = dict(attrs.get("Config") or {})
        health_payload = dict(state.get("Health") or {})
        return DockerContainerState(
            name=str(getattr(container, "name", name) or name),
            status=str(state.get("Status") or str(getattr(container, "status", "")) or ""),
            health=str(health_payload.get("Status") or ""),
            image=str(image_cfg.get("Image") or ""),
            exit_code=(
                int(state.get("ExitCode"))
                if isinstance(state.get("ExitCode"), int)
                and not isinstance(state.get("ExitCode"), bool)
                else None
            ),
        )
