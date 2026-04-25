"""Auto-heal job: detect, snapshot, restore, restart, audit.

The bug-driver: 2026-04-20 a Prowlarr SIGTERM-mid-write left
``/config/config.xml`` ending in ``</Config>sm>\n</Config>``.
Prowlarr crashlooped silently for hours; users had no signal that
new movies stopped being found and no way to fix it without
``kubectl exec`` and a hand-edited XML file.

Pipeline:

1. **Snapshot** — every cycle, for each service whose
   ``ConfigIntegrityService`` reports ``ok``, hash the live file
   and snapshot it if the hash changed since the previous one.
   Snapshots live under ``CONFIG_ROOT/.controller/snapshots/<id>/``
   with filenames ``<basename>.<unix_ts>.bak``. We keep the N most
   recent per service (default 5).
2. **Heal** — for each service whose integrity is ``corrupt``
   AND whose crashloop classification is ``healable``, restore
   the most recent matching snapshot, restart the workload, and
   write an audit-log entry.
3. The cycle is gated by ``CONTROLLER_AUTO_HEAL_ENABLED`` (default
   true). Each heal also carries a per-service throttle so a
   pathological loop (snapshot is also broken) doesn't burn the
   audit log."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable

from .config_integrity import ConfigIntegrityService
from .crashloop import CrashloopClassifier
from .registry import SERVICES, ServiceDef
from .workload_inspector import WorkloadInspector, build_default_inspector

_log = logging.getLogger("controller_api")


# ----------------------------------------------------------------------
# Snapshot store
# ----------------------------------------------------------------------


class SnapshotStore:
    """Manages on-disk snapshots of healthy config files.

    Layout::

        <root>/<service_id>/<basename>.<unix_ts>.bak

    Snapshots are immutable once written; the store only ever
    creates new ones and prunes oldest. Callers that want to know
    "is this content already snapshotted?" should call
    ``content_hash`` and compare against ``latest_hash``."""

    def __init__(
        self,
        root: Path,
        keep_per_service: int = 5,
    ) -> None:
        self._root = Path(root)
        self._keep = max(1, int(keep_per_service))

    def snapshot_dir(self, service_id: str) -> Path:
        d = self._root / service_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list_snapshots(
        self, service_id: str, basename: str | None = None,
    ) -> list[Path]:
        d = self._root / service_id
        if not d.is_dir():
            return []
        out = []
        for p in d.iterdir():
            if not p.is_file() or not p.name.endswith(".bak"):
                continue
            if basename and not p.name.startswith(basename + "."):
                continue
            out.append(p)
        out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return out

    def latest_snapshot(
        self, service_id: str, basename: str,
    ) -> Path | None:
        snaps = self.list_snapshots(service_id, basename=basename)
        return snaps[0] if snaps else None

    def latest_hash(
        self, service_id: str, basename: str,
    ) -> str | None:
        latest = self.latest_snapshot(service_id, basename)
        if latest is None:
            return None
        return self.content_hash(latest)

    @staticmethod
    def content_hash(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def save_snapshot(
        self, service_id: str, source: Path,
    ) -> Path:
        """Copy ``source`` into the snapshot dir with a timestamp
        suffix. Returns the path of the new snapshot. Always
        prunes the directory after writing."""
        d = self.snapshot_dir(service_id)
        # Microsecond resolution — snapshots taken in the same
        # second (common during tight test loops or fast cycles)
        # would otherwise collide on the same filename.
        ts = int(time.time() * 1_000_000)
        dst = d / f"{source.name}.{ts}.bak"
        # Use copy2 to preserve mtime so prune ordering is by
        # snapshot creation, not by the source file's mtime which
        # may be older than the snapshot.
        shutil.copy2(source, dst)
        # Force the snapshot's mtime to "now" so prune ordering is
        # unambiguous when many snapshots land in the same second.
        os.utime(dst, None)
        self._prune(service_id)
        return dst

    def restore(
        self, snapshot: Path, target: Path,
    ) -> None:
        """Restore ``snapshot`` to ``target`` atomically. Writes to
        ``target.heal-new``, fsyncs, renames into place. The
        target's parent directory must already exist."""
        tmp = target.with_suffix(target.suffix + ".heal-new")
        shutil.copy2(snapshot, tmp)
        with tmp.open("rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, target)

    def _prune(self, service_id: str) -> None:
        d = self._root / service_id
        if not d.is_dir():
            return
        # Group by basename so different files in the same service
        # don't crowd each other out of the keep window.
        by_basename: dict[str, list[Path]] = {}
        for p in d.iterdir():
            if not p.is_file() or not p.name.endswith(".bak"):
                continue
            # filename is "<original>.<ts>.bak" — strip the last
            # two dot-separated parts to recover the basename.
            parts = p.name.rsplit(".", 2)
            if len(parts) < 3:
                continue
            base = parts[0]
            by_basename.setdefault(base, []).append(p)
        for base, snaps in by_basename.items():
            snaps.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for old in snaps[self._keep:]:
                try:
                    old.unlink()
                except OSError:
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)


# ----------------------------------------------------------------------
# Heal events
# ----------------------------------------------------------------------


@dataclass
class HealEvent:
    """Record of one auto-heal action. Returned to the dashboard
    and written to the audit log."""

    service_id: str
    timestamp: float
    cause: str
    action: str         # "restored" | "skipped_no_snapshot" | "restore_failed"
    snapshot: str       # path of the snapshot that was used (or "")
    target: str         # path that was overwritten
    restarted: bool
    detail: str = ""


# ----------------------------------------------------------------------
# Auto-heal service
# ----------------------------------------------------------------------


# How long to wait between heal attempts for the same service. Without
# this, a heal that fails (e.g., snapshot is also bad) will retry on
# every cycle and flood the audit log.
_HEAL_THROTTLE_SECONDS = 5 * 60


class AutoHealService:

    def __init__(
        self,
        *,
        config_root: Path | None = None,
        integrity_svc: ConfigIntegrityService | None = None,
        classifier: CrashloopClassifier | None = None,
        snapshot_store: SnapshotStore | None = None,
        inspector: WorkloadInspector | None = None,
        services: list[ServiceDef] | None = None,
        restart_fn: Callable[[str], bool] | None = None,
        audit_fn: Callable[[HealEvent], None] | None = None,
        record_history_fn: Callable | None = None,
        enabled: bool | None = None,
        throttle_seconds: int = _HEAL_THROTTLE_SECONDS,
    ) -> None:
        self._config_root = Path(
            config_root if config_root is not None
            else os.environ.get("CONFIG_ROOT", "/srv-config")
        )
        snap_root = self._config_root / ".controller" / "snapshots"
        self._snapshots = snapshot_store or SnapshotStore(snap_root)
        self._services = list(services) if services is not None else list(SERVICES)
        self._integrity = integrity_svc or ConfigIntegrityService(
            config_root=self._config_root, services=self._services,
        )
        self._inspector = inspector or build_default_inspector()
        self._classifier = classifier or CrashloopClassifier(
            inspector=self._inspector, services=self._services,
        )
        self._restart_fn = restart_fn or _default_restart
        self._audit_fn = audit_fn or _default_audit
        # Late-import default: avoids a controller_main → auto_heal
        # → job_framework → controller_main cycle at module import
        # time. The job_framework module is small and already
        # imported by handlers_get for the same reason.
        if record_history_fn is None:
            try:
                from media_stack.cli.commands.job_framework import (
                    _record_history as _default_record_history,
                )
                record_history_fn = _default_record_history
            except Exception as exc:  # noqa: BLE001
                _log.debug(
                    "[DEBUG] auto-heal: default _record_history "
                    "unavailable (%s) — history tagging disabled",
                    exc,
                )
                record_history_fn = None
        self._record_history = record_history_fn
        self._throttle = int(throttle_seconds)
        self._last_heal_at: dict[str, float] = {}
        self._recent_events: list[HealEvent] = []
        self._lock = threading.Lock()

        if enabled is None:
            enabled = (os.environ.get("CONTROLLER_AUTO_HEAL_ENABLED", "true")
                       .strip().lower() not in {"0", "false", "no", "off"})
        self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    def recent_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [asdict(e) for e in self._recent_events[-limit:][::-1]]

    # ------------------------------------------------------------------
    # Snapshot pass
    # ------------------------------------------------------------------

    def snapshot_healthy(self) -> int:
        """For each service whose live config parses cleanly, take
        a snapshot if its content hash differs from the last
        snapshot. Returns the number of new snapshots written."""
        count = 0
        results = self._integrity.check_all()
        for svc in self._services:
            entry = results.get(svc.id, {})
            if entry.get("status") != "ok":
                continue
            cfg_path = Path(entry.get("file") or "")
            if not cfg_path.is_file():
                continue
            current_hash = SnapshotStore.content_hash(cfg_path)
            existing = self._snapshots.latest_hash(
                svc.id, cfg_path.name,
            )
            if existing == current_hash:
                continue
            try:
                self._snapshots.save_snapshot(svc.id, cfg_path)
                count += 1
            except OSError as exc:
                _log.warning(
                    "auto-heal: snapshot %s failed: %s", svc.id, exc,
                )
        return count

    # ------------------------------------------------------------------
    # Heal pass
    # ------------------------------------------------------------------

    def heal_corrupt(self) -> list[dict]:
        if not self._enabled:
            return []
        events: list[HealEvent] = []
        integrity = self._integrity.check_all()
        classification = self._classifier.check_all()
        now = time.time()
        for svc in self._services:
            ient = integrity.get(svc.id, {})
            cent = classification.get(svc.id, {})
            if ient.get("status") != "corrupt":
                continue
            # Trust either signal: a corrupt-on-disk file always
            # heals (it caused the crashloop). The classifier may
            # not have a 'healable' verdict yet because the pod
            # hasn't restarted enough times to log the pattern.
            cause = cent.get("cause") or "config_corrupt"
            target = Path(ient.get("file") or "")
            if not target:
                continue
            last_at = self._last_heal_at.get(svc.id, 0.0)
            if now - last_at < self._throttle:
                continue
            event = self._heal_one(svc, cause, target)
            self._last_heal_at[svc.id] = now
            with self._lock:
                self._recent_events.append(event)
                if len(self._recent_events) > 200:
                    self._recent_events = self._recent_events[-100:]
            events.append(event)
            try:
                self._audit_fn(event)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "auto-heal: audit write failed for %s: %s",
                    svc.id, exc,
                )
        return [asdict(e) for e in events]

    def _heal_one(
        self, svc: ServiceDef, cause: str, target: Path,
    ) -> HealEvent:
        snapshot = self._snapshots.latest_snapshot(svc.id, target.name)
        if snapshot is None:
            return HealEvent(
                service_id=svc.id,
                timestamp=time.time(),
                cause=cause,
                action="skipped_no_snapshot",
                snapshot="",
                target=str(target),
                restarted=False,
                detail="No prior snapshot — auto-heal needs at least one healthy "
                       "config to restore from. Try the dashboard's manual restore.",
            )
        try:
            self._snapshots.restore(snapshot, target)
        except (OSError, shutil.SameFileError) as exc:
            return HealEvent(
                service_id=svc.id,
                timestamp=time.time(),
                cause=cause,
                action="restore_failed",
                snapshot=str(snapshot),
                target=str(target),
                restarted=False,
                detail=f"restore failed: {exc}",
            )
        restarted = False
        try:
            restarted = bool(self._restart_fn(svc.id))
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "auto-heal: restart of %s raised %s", svc.id, exc,
            )
        return HealEvent(
            service_id=svc.id,
            timestamp=time.time(),
            cause=cause,
            action="restored",
            snapshot=str(snapshot),
            target=str(target),
            restarted=restarted,
            detail=f"Restored from snapshot taken "
                   f"{int(time.time() - snapshot.stat().st_mtime)}s ago.",
        )

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def run_cycle(self) -> dict:
        t0 = time.time()
        snapshots = self.snapshot_healthy()
        heals = self.heal_corrupt()
        # Guardrail evaluation rides the auto-heal cycle so we don't
        # spawn a second daemon thread (and so a stop-the-world heal
        # can pause guardrails too). Lazy-imported to avoid pulling
        # the registry into every test that constructs an
        # ``AutoHealService`` with a noop snapshot store.
        try:
            from media_stack.services import guardrails as _guardrails_pkg
            _guardrails_pkg.tick()
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] auto-heal: guardrail tick failed: %s", exc)
        elapsed = round(time.time() - t0, 2)
        # Surface the cycle in /api/jobs.history with an
        # ``auto-heal`` source tag whenever it actually took action
        # (snapshotted a config or healed a corrupt one). A
        # zero-effect tick fires every ~minute via the daemon
        # thread; recording every tick would flood the 20-entry
        # ring buffer and crowd out cron + manual runs the
        # operator actually cares about.
        if (snapshots or heals) and self._record_history is not None:
            try:
                ok = sum(1 for h in heals if h.get("action") == "restored")
                errors = sum(
                    1 for h in heals
                    if h.get("action") in ("restore_failed",)
                )
                skipped = sum(
                    1 for h in heals
                    if h.get("action") == "skipped_no_snapshot"
                )
                jobs: dict[str, dict] = {}
                for ev in heals:
                    svc = str(ev.get("service_id") or "?")
                    status = "ok" if ev.get("action") == "restored" else (
                        "skipped" if ev.get("action") == "skipped_no_snapshot"
                        else "error"
                    )
                    jobs[f"auto-heal:{svc}"] = {
                        "status": status, "elapsed": 0,
                    }
                if not jobs and snapshots:
                    jobs["auto-heal:snapshot"] = {
                        "status": "ok", "elapsed": elapsed,
                    }
                self._record_history(
                    {
                        "elapsed": elapsed,
                        "ok": ok or (1 if snapshots and not heals else 0),
                        "skipped": skipped,
                        "errors": errors,
                        "jobs": jobs,
                    },
                    source="auto-heal",
                )
            except Exception as exc:  # noqa: BLE001
                _log.debug(
                    "[DEBUG] auto-heal: history record failed: %s", exc,
                )
        return {
            "enabled": self._enabled,
            "snapshots_taken": snapshots,
            "heals_performed": heals,
            "ran_at": time.time(),
        }


# ----------------------------------------------------------------------
# Default restart + audit hooks
# ----------------------------------------------------------------------


def _default_restart(service_id: str) -> bool:
    """Best-effort restart. Tries Docker first (fast, in-process),
    then K8s pod-delete. Returns True if either worked."""
    try:
        import docker  # type: ignore
        client = docker.from_env()
        container = client.containers.get(service_id)
        container.restart(timeout=15)
        return True
    except Exception as exc:
        _log.debug("[DEBUG] auto-heal docker restart failed: %s", exc)
    try:
        from kubernetes import client, config  # type: ignore
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        v1 = client.CoreV1Api()
        ns = os.environ.get("K8S_NAMESPACE", "media-stack")
        pods = v1.list_namespaced_pod(
            namespace=ns, label_selector=f"app={service_id}",
        )
        for pod in pods.items:
            v1.delete_namespaced_pod(name=pod.metadata.name, namespace=ns)
        return bool(pods.items)
    except Exception as exc:
        _log.debug("[DEBUG] auto-heal k8s restart failed: %s", exc)
    return False


def _default_audit(event: HealEvent) -> None:
    """Write a heal event to the user-mgmt audit log. Falls back
    to silent skip if the user service isn't available — the heal
    still happened, we just don't have a chained record of it."""
    try:
        from media_stack.core.auth.users.user_service_factory import (
            build_default_service,
        )
        svc = build_default_service()
        if svc is None or not hasattr(svc, "_audit"):
            return
        svc._audit.append(
            actor="auto-heal",
            action="auto_heal",
            target=event.service_id,
            result="ok" if event.action == "restored" else event.action,
            detail={
                "cause": event.cause,
                "snapshot": event.snapshot,
                "target": event.target,
                "restarted": event.restarted,
                "explanation": event.detail,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("[DEBUG] auto-heal audit fallback skipped: %s", exc)


# ----------------------------------------------------------------------
# Module-level singleton + endpoint helpers
# ----------------------------------------------------------------------


_DEFAULT: AutoHealService | None = None
_DEFAULT_LOCK = threading.Lock()


def default() -> AutoHealService:
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = AutoHealService()
    return _DEFAULT


def status() -> dict:
    svc = default()
    return {
        "enabled": svc.enabled,
        "recent_events": svc.recent_events(),
    }


def run_cycle() -> dict:
    return default().run_cycle()


def set_enabled(value: bool) -> dict:
    svc = default()
    svc.set_enabled(value)
    return {"enabled": svc.enabled}
