"""Maintenance workflow services — config snapshots + stale-file pruning.

ADR-0015 Phase 5. Pre-Phase-5 these lived on a single
:class:`MaintenanceService` god-class in
``cli/commands/maintenance.py`` (128 LoC, one ``@staticmethod`` +
two unrelated public methods). Phase 5 splits the two
responsibilities into two SRP classes under the workflows tier:

* :class:`ConfigSnapshotService` — Repository for timestamped
  config snapshots with retention (keeps the last ``snapshot_limit``).
* :class:`StaleFilePruner` — Strategy chain for pruning the three
  file families that grow without bounds (XMLTV guides, media
  server logs, arr service logs).

The commands-tier ``maintenance.py`` survives as a re-export shim
so existing callers (:mod:`controller_serve` background timer,
:mod:`test_cli_commands_extended`) keep working without churn.

Constructor-injection: every IO dep (``config_root``, the
``log`` callback) is passed in. No ``os.environ`` reads inside
methods; the shim layer is the only place env defaults are
sampled.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from media_stack.core.logging_utils import log_swallowed
from media_stack.core.service_registry.registry import SERVICES


_API_KEY_REDACTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"<ApiKey>[^<]+</ApiKey>", "<ApiKey>***</ApiKey>"),
    (r"api_key\s*=\s*\S+", "api_key = ***"),
    (r'"apiKey"\s*:\s*"[^"]+"', '"apiKey": "***"'),
)

# Retention defaults match the pre-Phase-5 values in
# ``cli/commands/maintenance.py``. Operators can override
# per-instance via constructor args; the module-level constants
# remain the documented defaults.
SNAPSHOT_RETENTION_LIMIT = 24
XMLTV_KEEP_PER_DIR = 2
LOG_KEEP_PER_DIR = 5
BYTES_PER_MEBIBYTE = 1048576


@dataclass(frozen=True)
class _ConfigPath:
    """One entry in the snapshot-target registry view."""

    app_id: str
    relative_path: str

    @property
    def joined(self) -> str:
        return f"{self.app_id}/{self.relative_path}"


class ConfigSnapshotService:
    """Repository: take timestamped snapshots of service config files.

    Each snapshot file is a JSON map of ``"<app>/<rel>": <text>``
    written under ``<config_root>/.snapshots/snapshot-<YYYYMMDDTHHMMSS>.json``.
    API-key-shaped strings are redacted in-line so the snapshot
    can be archived alongside operator-readable troubleshooting
    data without leaking secrets.

    Retention: the service keeps the most-recent ``retention_limit``
    snapshots and unlinks older ones; the rotation runs after
    the new snapshot is written so a failure mid-write doesn't
    erase known-good history.
    """

    def __init__(
        self,
        config_root: Path,
        *,
        retention_limit: int = SNAPSHOT_RETENTION_LIMIT,
    ) -> None:
        self._config_root = config_root
        self._retention_limit = retention_limit

    def snapshot(self) -> Path:
        snapshot_dir = self._config_root / ".snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        captured = self._capture_redacted_payloads()
        ts = time.strftime("%Y%m%dT%H%M%S")
        out = snapshot_dir / f"snapshot-{ts}.json"
        out.write_text(json.dumps(captured, indent=2), encoding="utf-8")
        self._rotate(snapshot_dir)
        return out

    def snapshot_targets(self) -> tuple[_ConfigPath, ...]:
        """Build ``(app_id, relative_path)`` pairs from the service registry.

        Only includes text-based config files (not binary formats
        like sqlite). The set is derived from the registry's
        ``api_key_config`` field so a new service that registers
        ``api_key_config: "<id>/config.yaml"`` is picked up
        automatically.
        """
        return tuple(
            _ConfigPath(app_id=s.id, relative_path=s.api_key_config.split("/", 1)[1])
            for s in SERVICES
            if s.api_key_config and s.api_key_format != "sqlite"
        )

    def _capture_redacted_payloads(self) -> dict[str, str]:
        captured: dict[str, str] = {}
        for target in self.snapshot_targets():
            path = self._config_root / target.app_id / target.relative_path
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                for pattern, replacement in _API_KEY_REDACTION_PATTERNS:
                    text = re.sub(pattern, replacement, text)
                captured[target.joined] = text
            except OSError as exc:
                # File-system error on a single target shouldn't abort
                # the rest of the snapshot. Narrowed from the pre-
                # Phase-5 broad ``except Exception`` to OSError only,
                # which is what ``read_text`` / I/O paths can raise.
                log_swallowed(exc)
        return captured

    def _rotate(self, snapshot_dir: Path) -> None:
        existing = sorted(snapshot_dir.glob("snapshot-*.json"), reverse=True)
        for old in existing[self._retention_limit:]:
            old.unlink(missing_ok=True)


class StaleFilePruner:
    """Strategy chain: prune files that grow without bounds.

    Three strategies, run in order, each over a different file
    family:

    1. **XMLTV guides** under media-server transcode caches —
       keep the most recent two; the rest are stale guide pulls.
    2. **Media-server logs** under ``<config>/<media-server>/log/`` —
       keep the most recent ``LOG_KEEP_PER_DIR`` per server.
    3. **Arr-service logs** under ``<config>/<arr>/logs/`` —
       same retention, but the arr family writes both ``.txt``
       and ``.log`` so both globs are walked.

    Each strategy returns its own pruned-file count; ``prune()``
    returns the total. Failures on a single file are swallowed via
    :func:`log_swallowed` so a permission-denied unlink doesn't
    abort the rest of the cleanup.
    """

    def __init__(
        self,
        config_root: Path,
        log: Callable[[str], None],
    ) -> None:
        self._config_root = config_root
        self._log = log

    def prune(self) -> int:
        pruned = (
            self._prune_xmltv_guides()
            + self._prune_media_server_logs()
            + self._prune_arr_service_logs()
        )
        if pruned:
            self._log(f"[INFO] Stale file cleanup: pruned {pruned} files")
        return pruned

    def _media_server_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in SERVICES if s.category == "media" and s.host)

    def _arr_service_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in SERVICES if s.api_key_format == "xml")

    def _xmltv_search_paths(self) -> tuple[Path, ...]:
        candidates: list[Path] = [
            self._config_root.parent / "data" / "transcode" / "xmltv",
            Path("/srv-stack/data/transcode/xmltv"),
            Path("/cache/xmltv"),
        ]
        candidates.extend(
            self._config_root / ms_id / "data" / "xmltv"
            for ms_id in self._media_server_ids()
        )
        return tuple(candidates)

    def _prune_xmltv_guides(self) -> int:
        pruned = 0
        for xmltv_dir in self._xmltv_search_paths():
            if not xmltv_dir.is_dir():
                continue
            xmls = sorted(
                xmltv_dir.glob("*.xml"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            for old in xmls[XMLTV_KEEP_PER_DIR:]:
                try:
                    sz = old.stat().st_size
                    old.unlink()
                    pruned += 1
                    self._log(
                        f"[INFO] Pruned stale XMLTV guide: {old.name} "
                        f"({sz // BYTES_PER_MEBIBYTE}MB)"
                    )
                except OSError as exc:
                    log_swallowed(exc)
        return pruned

    def _prune_media_server_logs(self) -> int:
        pruned = 0
        for ms_id in self._media_server_ids():
            log_dir = self._config_root / ms_id / "log"
            if not log_dir.is_dir():
                continue
            logs = sorted(
                log_dir.glob("*.log"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            for old in logs[LOG_KEEP_PER_DIR:]:
                try:
                    old.unlink()
                    pruned += 1
                except OSError as exc:
                    log_swallowed(exc)
        return pruned

    def _prune_arr_service_logs(self) -> int:
        pruned = 0
        for app in self._arr_service_ids():
            log_dir = self._config_root / app / "logs"
            if not log_dir.is_dir():
                continue
            app_logs = sorted(
                log_dir.glob("*.txt"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            app_logs += sorted(
                log_dir.glob("*.log"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            for old in app_logs[LOG_KEEP_PER_DIR:]:
                try:
                    old.unlink()
                    pruned += 1
                except OSError as exc:
                    log_swallowed(exc)
        return pruned


__all__ = [
    "ConfigSnapshotService",
    "StaleFilePruner",
    "SNAPSHOT_RETENTION_LIMIT",
    "XMLTV_KEEP_PER_DIR",
    "LOG_KEEP_PER_DIR",
]
