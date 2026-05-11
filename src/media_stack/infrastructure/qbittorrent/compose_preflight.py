"""Compose preflight hook for qBittorrent.

Post-ADR-0013 Phase 3b cutover this is a thin deploy-time shim:
the actual WebUI credential rotation + reverse-proxy trust settings
(:data:`QBITTORRENT_REVERSE_PROXY_TRUST_PREFS`) live in
:meth:`QbittorrentLifecycle.ensure_credentials`, so the orchestrator
ticking the lifecycle on a reconcile loop and the compose-deploy
preflight ticking it once at install both run the same body — no
duplicate "log-in via exec, run setPreferences" machinery.

What this shim still owns:

* Resolving STACK_ADMIN_* defaults from ``compose_env`` and persisting
  them into the compose ``.env`` file (:class:`ComposeEnvFileWriter`).
  The lifecycle reads the credentials from ``ctx.secrets``; this is
  what populates them on first deploy.
* Adapting the resolver-supplied ``docker`` handle into a
  :class:`ComposeContainerAccess` so the lifecycle's
  ``container_access`` extra is the same Protocol on every code
  path (deploy preflight + orchestrator reconcile).
* Translating the lifecycle's typed ``Outcome`` failure back into a
  ``RuntimeError`` with the legacy phase-tag shape callers of
  ``ensure_compose_torrent_client_credentials`` still expect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from media_stack.adapters.qbittorrent.lifecycle import QbittorrentLifecycle
from media_stack.domain.services import OrchestrationContext
from media_stack.infrastructure.platforms.compose.container_access import (
    ComposeContainerAccess,
)

InfoFn = Callable[[str], None]

_DEFAULT_NAMESPACE = "media-stack"
_DEFAULT_STACK_USERNAME = "admin"


class ComposeEnvFileWriter:
    """Upserts ``KEY=VALUE`` rows in a compose ``.env`` file.

    Kept separate from the preflight class because the operation is
    purely about file I/O — it has no knowledge of qBittorrent. Could
    be lifted to a shared helper if another compose preflight needs
    the same shape (today only qB does, so it lives here).
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def upsert(self, updates: dict[str, str]) -> None:
        if not updates:
            return
        lines = (
            self._path.read_text(encoding="utf-8").splitlines()
            if self._path.exists()
            else []
        )
        key_to_index = self._index_by_key(lines)
        for key, value in updates.items():
            row = f"{key}={value}"
            if key in key_to_index:
                lines[key_to_index[key]] = row
            else:
                lines.append(row)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(lines).rstrip() + "\n"
        self._path.write_text(payload, encoding="utf-8")

    def _index_by_key(self, lines: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for idx, raw_line in enumerate(lines):
            line = str(raw_line or "").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.partition("=")[0].strip()
            if key:
                out[key] = idx
        return out


class QbittorrentComposePreflight:
    """Compose-deploy preflight for qBittorrent.

    Resolves stack-admin credentials, writes them to the compose env
    file, then dispatches to
    :meth:`QbittorrentLifecycle.ensure_credentials` for the actual
    WebUI work (credential rotation + reverse-proxy trust preferences).
    The dispatch goes through the same ``OrchestrationContext`` shape
    the orchestrator's reconcile loop uses, so deploy-time and
    runtime-tick code paths share one rotation body.
    """

    def __init__(
        self,
        *,
        lifecycle: QbittorrentLifecycle | None = None,
        default_namespace: str = _DEFAULT_NAMESPACE,
        default_username: str = _DEFAULT_STACK_USERNAME,
    ) -> None:
        self._lifecycle = lifecycle or QbittorrentLifecycle()
        self._default_namespace = default_namespace
        self._default_username = default_username

    def ensure_compose_torrent_client_credentials(
        self,
        *,
        compose_env: dict[str, str],
        compose_env_file: Path | None,
        namespace: str,
        docker: Any,
        info: InfoFn,
        **_: object,
    ) -> dict[str, str]:
        username, password = self._resolve_stack_admin(compose_env, namespace)
        compose_env["STACK_ADMIN_USERNAME"] = username
        compose_env["STACK_ADMIN_PASSWORD"] = password

        if compose_env_file is not None:
            ComposeEnvFileWriter(compose_env_file).upsert({
                "STACK_ADMIN_USERNAME": username,
                "STACK_ADMIN_PASSWORD": password,
            })

        container = docker.get_container("qbittorrent")
        if container is None:
            # ``compose up`` hasn't reached the qB service yet —
            # the orchestrator's first post-up reconcile tick
            # picks it up via the contract Job
            # ``qbittorrent:ensure-credentials``.
            info(
                "Compose torrent-client preflight: container 'qbittorrent' "
                "not found; skipping credential sync (orchestrator will "
                "run qbittorrent:ensure-credentials once compose `up` "
                "completes).",
            )
            return {
                "STACK_ADMIN_USERNAME": username,
                "STACK_ADMIN_PASSWORD": password,
            }

        outcome = self._lifecycle.ensure_credentials(
            self._build_context(
                username=username,
                password=password,
                container=container,
            ),
        )
        if outcome.ok:
            self._log_success(outcome, info)
            return {
                "STACK_ADMIN_USERNAME": username,
                "STACK_ADMIN_PASSWORD": password,
            }

        # Translate typed-Outcome failure into the legacy RuntimeError
        # shape with a phase-tag so existing deploy callers see the
        # same error contract.
        evidence = dict(outcome.evidence or {})
        phase = (
            evidence.get("phase")
            or evidence.get("rotation_reason")
            or "verify"
        )
        raise RuntimeError(
            f"Compose torrent-client preflight failed at {phase}: "
            f"{outcome.error or 'unknown error'}",
        )

    def _resolve_stack_admin(
        self,
        compose_env: dict[str, str],
        namespace: str,
    ) -> tuple[str, str]:
        ns = str(namespace or "").strip() or self._default_namespace
        username = (
            str(compose_env.get("STACK_ADMIN_USERNAME") or "").strip()
            or self._default_username
        )
        password = (
            str(compose_env.get("STACK_ADMIN_PASSWORD") or "").strip()
            or ns
        )
        return username, password

    def _build_context(
        self,
        *,
        username: str,
        password: str,
        container: Any,
    ) -> OrchestrationContext:
        return OrchestrationContext(
            service_id="qbittorrent",
            config={
                "host": "qbittorrent",
                "port": 8080,
                "scheme": "http",
                "login_path": "/api/v2/auth/login",
            },
            secrets={
                "STACK_ADMIN_USERNAME": username,
                "STACK_ADMIN_PASSWORD": password,
            },
            extra={
                "container_access": ComposeContainerAccess(container),
            },
        )

    def _log_success(self, outcome: Any, info: InfoFn) -> None:
        evidence = outcome.evidence or {}
        if evidence.get("rotated"):
            info(
                "Compose torrent-client preflight: rotated qBittorrent "
                "WebUI credentials + applied reverse-proxy trust prefs "
                "(via lifecycle).",
            )
        else:
            info(
                "Compose torrent-client preflight: stack-admin credentials "
                "+ trust prefs already valid for qBittorrent.",
            )


# Module-level singleton + handler shim — the contract YAML's
# ``plugin.compose_preflight_handler`` reference resolves to this
# callable. Constructing the singleton at import keeps the resolver
# fast (no class instantiation per dispatch) while leaving the
# classes themselves testable in isolation.
_instance = QbittorrentComposePreflight()
ensure_compose_torrent_client_credentials = (
    _instance.ensure_compose_torrent_client_credentials
)

__all__ = [
    "ComposeEnvFileWriter",
    "QbittorrentComposePreflight",
    "ensure_compose_torrent_client_credentials",
]
