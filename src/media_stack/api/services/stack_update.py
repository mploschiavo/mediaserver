"""Stack-update service: check the registry for a newer controller
image tag and (optionally) trigger an in-place compose upgrade.

Why this exists: end users were stuck running the CLI release dance
(``git pull && bin/release.sh && docker compose up -d --force-recreate``)
to pick up new versions. Most home-stack operators don't want to
think about CLI commands. This service surfaces "v1.0.100 is
available" in the dashboard and (if ``STACK_UPDATE_ALLOW_INPLACE``
is enabled) does the upgrade with a single click.

Two pieces:

1. ``check_for_update()`` — polls the Harbor v2 registry tags
   endpoint, parses semver, returns the highest tag > current
   ``VERSION``. Cached for 5 minutes so the dashboard can poll it
   freely without hammering the registry.

2. ``start_upgrade()`` — spawns a *sibling* container (image
   ``docker:cli``, mounted with the host docker socket and the
   dist/ directory) that runs ``docker compose pull && up -d
   --force-recreate``. The controller dies briefly when the new
   controller image is pulled, but the upgrader sibling keeps
   running and the new controller comes up. ``upgrade_status()``
   reports the sibling's exit code so the dashboard can surface
   success/failure after the controller comes back.

In-place upgrade is gated behind ``STACK_UPDATE_ALLOW_INPLACE``
because it touches the docker socket. Without the flag, the
banner still surfaces "update available" with manual-upgrade
instructions — that's the safe default.

ADR-0012: behaviour lives on ``StackUpdateService``; module-level
aliases (``check_for_update`` / ``start_upgrade`` / ``upgrade_status``
plus the underscore helpers) preserve the existing import surface
and keep ``mock.patch("…stack_update.check_for_update")`` working —
internal calls dispatch through ``sys.modules[__name__]`` so an
external patch on a public name flows into ``start_upgrade`` etc."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger("media_stack.stack_update")

_REGISTRY = os.environ.get(
    "STACK_UPDATE_REGISTRY", "harbor.iomio.io/public/media-stack-controller"
)
_TAGS_URL = (
    "https://"
    + _REGISTRY.split("/", 1)[0]
    + "/v2/"
    + _REGISTRY.split("/", 1)[1]
    + "/tags/list"
)
_GITHUB_REPO = os.environ.get(
    "STACK_UPDATE_GITHUB_REPO", "mploschiavo/mediaserver"
)
_VERSION_FILE = Path("/opt/media-stack/VERSION")
_CACHE_TTL_SECONDS = 300

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {}
_upgrade_lock = threading.Lock()
_upgrade_state: dict[str, Any] = {"task_id": "", "status": "idle"}


class StackUpdateService:
    """Registry-probe + in-place-upgrade orchestration.

    All methods are plain instance methods (no ``@staticmethod``)
    so subclasses / tests can swap behaviour wholesale via a
    different instance if needed. Module-level aliases below
    expose the public surface and keep the historical import
    surface (``from … import stack_update; stack_update.check_for_update()``)
    intact for both production callers and ``mock.patch`` targets."""

    def _current_version(self) -> str:
        if _VERSION_FILE.is_file():
            try:
                return _VERSION_FILE.read_text(encoding="utf-8").strip()
            except OSError:
                logging.getLogger("media_stack").debug(
                    "[DEBUG] Swallowed exception", exc_info=True,
                )
        return os.environ.get("STACK_VERSION", "0.0.0")

    def _semver_tuple(self, tag: str) -> tuple[int, int, int] | None:
        m = _SEMVER.match(tag.strip())
        if not m:
            return None
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def _fetch_registry_tags(self, timeout: float = 5.0) -> list[str]:
        import urllib.request
        req = urllib.request.Request(
            _TAGS_URL, headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
            tags = body.get("tags") or []
            return [str(t) for t in tags]
        except Exception as exc:
            _log.debug("registry tag fetch failed: %s", exc)
            return []

    def _allow_inplace(self) -> bool:
        val = os.environ.get("STACK_UPDATE_ALLOW_INPLACE", "").strip().lower()
        return val in ("1", "true", "yes", "on")

    def check_for_update(self, force: bool = False) -> dict[str, Any]:
        """Returns:
            {current: "1.0.99", latest: "1.0.100", upgradable: bool,
             release_url: "...github.com/.../releases/tag/v1.0.100",
             allow_inplace: bool, last_checked_epoch: int}

        Result is cached for ``_CACHE_TTL_SECONDS`` so the dashboard
        can poll on every page load without hammering the registry."""
        mod = sys.modules[__name__]
        current = mod._current_version()
        now = int(time.time())
        with _cache_lock:
            cached = dict(_cache) if _cache else None
        if (
            not force
            and cached
            and now - int(cached.get("last_checked_epoch") or 0) < _CACHE_TTL_SECONDS
            and cached.get("current") == current
        ):
            return cached

        tags = mod._fetch_registry_tags()
        current_t = mod._semver_tuple(current) or (0, 0, 0)
        candidates: list[tuple[tuple[int, int, int], str]] = []
        for t in tags:
            st = mod._semver_tuple(t)
            if st and st > current_t:
                candidates.append((st, t.lstrip("v")))
        candidates.sort()
        latest = candidates[-1][1] if candidates else current
        upgradable = bool(candidates)
        out = {
            "current": current,
            "latest": latest,
            "upgradable": upgradable,
            "release_url": (
                f"https://github.com/{_GITHUB_REPO}/releases/tag/v{latest}"
                if upgradable else ""
            ),
            "allow_inplace": mod._allow_inplace(),
            "last_checked_epoch": now,
        }
        with _cache_lock:
            _cache.clear()
            _cache.update(out)
        return out

    def start_upgrade(self, target_tag: str | None = None) -> dict[str, Any]:
        """Spawn a sibling docker:cli container that runs ``docker
        compose pull && up -d --force-recreate``.  Returns immediately
        with a task id; caller polls ``upgrade_status()``.

        No-op + 403-style response when ``STACK_UPDATE_ALLOW_INPLACE``
        is not set — exposing this without explicit opt-in would let
        anyone with admin to the dashboard restart every container in
        the stack remotely."""
        mod = sys.modules[__name__]
        if not mod._allow_inplace():
            return {
                "accepted": False,
                "error": (
                    "In-place upgrade is disabled. Set "
                    "STACK_UPDATE_ALLOW_INPLACE=true on the controller "
                    "container to enable."
                ),
            }
        info = mod.check_for_update()
        if not info["upgradable"]:
            return {
                "accepted": False,
                "error": f"Already on the latest version ({info['current']}).",
            }

        try:
            import docker
        except ImportError:
            return {
                "accepted": False,
                "error": "docker SDK not available in controller image.",
            }

        client = docker.from_env()
        # Find the compose project working_dir from the controller's own
        # labels — that's where docker-compose.yml lives on the host.
        try:
            me = client.containers.get("media-stack-controller")
        except Exception:
            return {"accepted": False, "error": "controller container not found"}
        labels = me.labels or {}
        work_dir_host = labels.get("com.docker.compose.project.working_dir") or ""
        compose_file = labels.get("com.docker.compose.project.config_files") or ""
        if not work_dir_host or not compose_file:
            return {
                "accepted": False,
                "error": "missing compose project labels — can't locate dist/",
            }

        task_id = f"upgrade-{int(time.time())}"
        cmd = (
            f"set -e; cd /work && "
            f"docker compose pull media-stack-controller && "
            f"docker compose up -d --force-recreate media-stack-controller && "
            f"echo OK"
        )
        try:
            # Remove any prior upgrader container so the name is free.
            try:
                old = client.containers.get("media-stack-upgrader")
                old.remove(force=True)
            except Exception:
                logging.getLogger("media_stack").debug(
                    "[DEBUG] Swallowed exception", exc_info=True,
                )
            client.containers.run(
                image="docker:27-cli",
                name="media-stack-upgrader",
                command=["sh", "-c", cmd],
                volumes={
                    "/var/run/docker.sock": {
                        "bind": "/var/run/docker.sock", "mode": "rw",
                    },
                    work_dir_host: {"bind": "/work", "mode": "ro"},
                },
                detach=True,
                auto_remove=False,
                network_mode="bridge",
            )
        except Exception as exc:
            _log.exception("failed to spawn upgrader sibling")
            return {"accepted": False, "error": str(exc)}

        with _upgrade_lock:
            _upgrade_state.update({
                "task_id": task_id,
                "status": "running",
                "started_epoch": int(time.time()),
                "target": target_tag or info["latest"],
            })
        return {"accepted": True, "task_id": task_id, "target": info["latest"]}

    def upgrade_status(self, task_id: str | None = None) -> dict[str, Any]:
        """Read the current upgrade-task state. Caller passes the
        ``task_id`` returned from ``start_upgrade``; we keep state in
        process so a different task_id from a stale browser tab gets
        a clear "not yours" answer."""
        with _upgrade_lock:
            state = dict(_upgrade_state)
        if not state.get("task_id"):
            return {"status": "idle"}
        if task_id and task_id != state.get("task_id"):
            return {"status": "stale", "current_task": state.get("task_id")}
        try:
            import docker
            client = docker.from_env()
            c = client.containers.get("media-stack-upgrader")
            c.reload()
            if c.status == "exited":
                exit_code = c.attrs.get("State", {}).get("ExitCode", -1)
                logs = c.logs(tail=20).decode("utf-8", errors="replace")
                with _upgrade_lock:
                    _upgrade_state["status"] = (
                        "complete" if exit_code == 0 else "failed"
                    )
                    _upgrade_state["exit_code"] = exit_code
                    _upgrade_state["log_tail"] = logs[-2000:]
                return dict(_upgrade_state)
            return {**state, "status": "running"}
        except Exception:
            return {**state, "status": "unknown"}


_INSTANCE = StackUpdateService()

# Public API — preserved as module-level names so existing imports
# (``from media_stack.api.services import stack_update``) and
# ``mock.patch("…services.stack_update.<name>")`` keep working.
check_for_update = _INSTANCE.check_for_update
start_upgrade = _INSTANCE.start_upgrade
upgrade_status = _INSTANCE.upgrade_status

# Internal helpers retain module-level aliases so the
# ``sys.modules[__name__]`` dispatch inside ``check_for_update`` /
# ``start_upgrade`` resolves them and ``mock.patch`` on a helper
# (e.g. for fault injection) replaces what callers see.
_current_version = _INSTANCE._current_version
_semver_tuple = _INSTANCE._semver_tuple
_fetch_registry_tags = _INSTANCE._fetch_registry_tags
_allow_inplace = _INSTANCE._allow_inplace
