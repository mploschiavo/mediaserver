"""Compose-side implementation of the ``ContainerAccess`` domain port.

Wraps a docker-py ``Container`` so the lifecycle layer can read
logs / exec shell commands / restart the service without
depending on docker-py directly. ADR-0013 Phase 3.

The orchestrator's ``LifecycleResolver.context_for`` populates
``OrchestrationContext.extra["container_access"]`` with one of these
when the platform is compose; lifecycle methods that need container
access ``cast(ContainerAccess, ctx.extra.get("container_access"))``
and check for ``None`` (k8s lifecycle methods get a different impl
behind the same Protocol; promises that don't need container access
just don't read ``extra["container_access"]``).
"""

from __future__ import annotations

from typing import Any, Mapping

from media_stack.domain.services.container_access import (
    ContainerAccess,
    ContainerAccessError,
)


class ComposeContainerAccess:
    """``ContainerAccess`` backed by a docker-py ``Container`` handle.

    Constructor takes the container handle (a ``docker.models.
    containers.Container``) so this class is testable without
    spinning a real daemon — tests inject a mock that satisfies the
    same shape (``logs``, ``exec_run``, ``restart`` methods).

    Stateless beyond the injected handle. The handle is itself a
    cached client object; re-resolving it across ticks is fine but
    wasteful, so ``LifecycleResolver`` keeps one per service.
    """

    def __init__(self, container: Any) -> None:
        self._container = container

    def read_logs(self, *, tail_lines: int = 600) -> str:
        try:
            raw = self._container.logs(
                stdout=True, stderr=True, tail=int(max(1, tail_lines)),
            )
        except Exception as exc:  # noqa: BLE001 — docker-py errors aren't typed
            raise ContainerAccessError(
                f"docker logs failed: {exc}"
            ) from exc
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw or "")

    def exec_shell(
        self,
        script: str,
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> tuple[int, str]:
        try:
            result = self._container.exec_run(
                cmd=["sh", "-lc", script],
                environment=dict(env or {}),
                stdout=True,
                stderr=True,
            )
        except Exception as exc:  # noqa: BLE001 — docker-py errors aren't typed
            raise ContainerAccessError(
                f"docker exec failed: {exc}"
            ) from exc
        raw_code = getattr(result, "exit_code", 1)
        code = int(raw_code if raw_code is not None else 1)
        raw_output = getattr(result, "output", b"")
        if isinstance(raw_output, bytes):
            output = raw_output.decode("utf-8", errors="replace")
        else:
            output = str(raw_output or "")
        return code, output

    def restart(self, *, timeout_seconds: int = 10) -> bool:
        try:
            self._container.restart(timeout=int(max(1, timeout_seconds)))
        except Exception:  # noqa: BLE001
            return False
        return True


# Compile-time + runtime check that the class satisfies the Protocol.
def _typecheck(value: ComposeContainerAccess) -> ContainerAccess:
    return value


__all__ = ["ComposeContainerAccess"]
