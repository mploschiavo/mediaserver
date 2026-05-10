"""Domain port for platform-agnostic container access.

ADR-0013 Phase 3 introduced this port so lifecycle ensurers can
read container logs / exec shell commands / restart services
without depending on docker-py or kubectl directly. The
infrastructure layer ships compose and kubernetes implementations;
the lifecycle method consumes the Protocol.

The orchestrator threads a concrete ``ContainerAccess`` through
``OrchestrationContext.extra["container_access"]`` (a `Mapping[str,
Any]`) — that's the existing escape hatch for "things adapters need
that aren't config or secrets," documented in
``OrchestrationContext``'s docstring. We deliberately do NOT add a
top-level ``container_access`` field to the frozen dataclass: the
field would be optional for ~90% of ensurers and mandatory for the
new rotation path, which is a worse contract than a ``Mapping``
lookup the lifecycle method does once.

The Protocol lives in ``domain/`` because it's the *contract*; the
docker-py and kubectl implementations live in ``infrastructure/``.
ADR-0011 leaf invariant: domain has zero outward imports, so this
file imports only from ``typing``.
"""

from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable


@runtime_checkable
class ContainerAccess(Protocol):
    """Platform-agnostic access to a service's container.

    The three methods cover what every existing lifecycle rotation
    or recovery path needs:

      * ``read_logs`` — tail the container's combined stdout+stderr.
        Used to recover ephemeral startup credentials (qBittorrent's
        temporary password is the canonical case).
      * ``exec_shell`` — run a one-line shell command inside the
        container with optional env. Returns ``(exit_code,
        stdout+stderr)``. Used for credential reset operations that
        require localhost-loopback access to the service's API
        (which from outside the container would hit auth-bypass
        rules that reject non-loopback origins).
      * ``restart`` — bounce the container. Used after on-disk
        config edits that need a process restart to take effect.

    Implementations are stateless wrappers around the per-platform
    container handle (docker-py ``Container`` for compose, k8s API
    pod handle for kubernetes). The orchestrator caches them per-
    service inside ``LifecycleResolver`` because re-resolving on
    every tick is wasteful.

    Methods raise ``ContainerAccessError`` (or a subclass) on
    irrecoverable failure; transient failures (container restarting,
    exec timeout) return non-zero exit codes / empty log strings so
    the lifecycle method can classify and retry.
    """

    def read_logs(self, *, tail_lines: int = 600) -> str:
        """Return the container's combined stdout+stderr log buffer.

        ``tail_lines`` is a hint, not a hard guarantee — the
        underlying platform may return fewer lines if the container
        has only just started. Empty string means "no logs available
        right now" (caller can retry or treat as transient).
        """
        ...

    def exec_shell(
        self,
        script: str,
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> tuple[int, str]:
        """Run ``script`` in a ``sh -lc`` subshell inside the container.

        Returns ``(exit_code, output)`` where output is combined
        stdout+stderr. ``env`` is added to the subshell's environment
        (use this for credentials — they don't appear in the
        ``script`` string and so don't leak to ``ps``-watching
        sidecars). ``timeout_seconds`` bounds the exec; on timeout
        returns a non-zero exit code and partial output.
        """
        ...

    def restart(self, *, timeout_seconds: int = 10) -> bool:
        """Bounce the container with a graceful-stop deadline.

        Returns True on successful restart, False if the platform
        rejected the request (container missing, daemon error). Does
        NOT wait for the container to become healthy after restart —
        callers that need that should poll their own probe.
        """
        ...


class ContainerAccessError(Exception):
    """Irrecoverable container access failure.

    Distinct from ``OSError`` so lifecycle methods can catch this
    specifically and translate to ``Outcome.failure(transient=False,
    ...)`` without swallowing unrelated I/O errors.
    """


__all__ = ["ContainerAccess", "ContainerAccessError"]
