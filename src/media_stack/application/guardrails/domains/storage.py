"""Storage-domain guardrails.

Each rule is a tiny dataclass that satisfies the ``Guardrail`` Protocol.
The default thresholds reflect the values the existing
``disk_guardrails_service`` ships with, so converting an operator's
existing ``disk_guardrails`` JSON to per-rule overrides is a no-op
upgrade.

State expected on the ``state`` mapping:

- ``state["disk"]`` — output of ``api.services.disk.get_disk()``,
  one entry per mount point with ``percent_used``, ``free_bytes``,
  ``total_bytes``.
- ``state["storage_breakdown"]`` — output of
  ``get_storage_breakdown()`` keyed by media-type folder name.
- ``state["mount_inodes"]`` — optional dict ``mount → percent_used``
  (state collector populates from ``os.statvfs`` when available).
- ``state["unpacker_scratch"]`` — ``{free_bytes, in_flight_bytes}``
  (placeholder when unpacker telemetry isn't wired yet).
- ``state["arr_recycle_bins"]`` — ``[{age_days, count}, ...]``.
- ``state["snapshots"]`` — ``{count, oldest_age_days}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from media_stack.domain.guardrails.protocols import Action, Severity

from ..registry import register_guardrail


_DOMAIN = "storage"


@dataclass
class _PerMountThreshold:
    id: str = "storage:per_mount_threshold"
    domain: str = _DOMAIN
    description: str = (
        "Warns when any monitored mount exceeds the per-mount used-percent "
        "threshold. Critical above max, warning above target."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {
            "max_percent": 85.0,
            "target_percent": 75.0,
            "per_mount": {},  # operator overrides keyed by mount label
        }
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        disks = state.get("disk") or {}
        if not isinstance(disks, dict):
            return None
        max_pct = float(threshold.get("max_percent", 85.0))
        target_pct = float(threshold.get("target_percent", 75.0))
        per_mount = threshold.get("per_mount") or {}
        worst: float = 0.0
        for label, info in disks.items():
            if not isinstance(info, dict):
                continue
            pct = float(info.get("percent_used") or 0.0)
            override = per_mount.get(label, {}) if isinstance(per_mount, dict) else {}
            mt = float(override.get("max_percent", max_pct))
            tt = float(override.get("target_percent", target_pct))
            if pct > mt:
                return "critical"
            if pct > tt:
                worst = max(worst, pct - tt)
        if worst > 0:
            return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        # Per-mount excess defers to the existing qBit cleanup
        # service. The registry plumbs that through state so this
        # rule stays I/O-free.
        if state.get("_qbit_cleanup_invoked"):
            return Action(rule_id=self.id, action="qbit_cleanup",
                          ok=True, detail="cleanup already invoked this tick")
        return Action(rule_id=self.id, action="qbit_cleanup",
                      ok=False, detail="schedule qBit cleanup pass")

    def current_value(self, state: Mapping[str, Any]) -> dict[str, float]:
        disks = state.get("disk") or {}
        if not isinstance(disks, dict):
            return {}
        return {
            label: float(info.get("percent_used") or 0.0)
            for label, info in disks.items()
            if isinstance(info, dict)
        }


@dataclass
class _FreeSpaceFloor:
    id: str = "storage:free_space_floor"
    domain: str = _DOMAIN
    description: str = (
        "Fires when any mount has less than the configured GiB of free "
        "space, even if percent-used is fine on a very large volume."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"min_free_gib": 10.0}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        floor_bytes = float(threshold.get("min_free_gib", 10.0)) * (1024 ** 3)
        disks = state.get("disk") or {}
        if not isinstance(disks, dict):
            return None
        for info in disks.values():
            if not isinstance(info, dict):
                continue
            free = float(info.get("free_bytes") or 0)
            if free <= 0:
                continue
            if free < floor_bytes:
                # Below half the floor → critical, otherwise warning.
                if free < floor_bytes * 0.5:
                    return "critical"
                return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify",
                      ok=False, detail="free space below floor; notify operator")

    def current_value(self, state: Mapping[str, Any]) -> dict[str, int]:
        disks = state.get("disk") or {}
        if not isinstance(disks, dict):
            return {}
        return {
            label: int(info.get("free_bytes") or 0)
            for label, info in disks.items()
            if isinstance(info, dict)
        }


@dataclass
class _PerContentTypeQuota:
    id: str = "storage:per_content_type_quota"
    domain: str = _DOMAIN
    description: str = (
        "Operator-defined GB ceilings for /srv-stack/media/{movies,tv,music,"
        "books}. A breach signals a runaway library."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {
            "ceilings_gb": {
                # Disabled by default — set per-deployment via the UI.
                "movies": 0,
                "tv": 0,
                "music": 0,
                "books": 0,
            }
        }
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        ceilings = threshold.get("ceilings_gb") or {}
        if not isinstance(ceilings, dict):
            return None
        breakdown = state.get("storage_breakdown") or {}
        if not isinstance(breakdown, dict):
            return None
        worst: Severity | None = None
        for name, ceiling in ceilings.items():
            try:
                ceiling_bytes = float(ceiling) * (1024 ** 3)
            except (TypeError, ValueError):
                continue
            if ceiling_bytes <= 0:
                continue
            actual = float(breakdown.get(name) or 0)
            if actual > ceiling_bytes:
                # Above 1.2× ceiling → critical.
                if actual > ceiling_bytes * 1.2:
                    return "critical"
                worst = "warning"
        return worst

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="content-type ceiling breached; review library policies")


@dataclass
class _InodeFloor:
    id: str = "storage:inode_floor"
    domain: str = _DOMAIN
    description: str = (
        "Fires when inode usage is above the threshold — small-file "
        "libraries (music, books) can run out of inodes long before bytes."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_percent": 90.0}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        max_pct = float(threshold.get("max_percent", 90.0))
        inodes = state.get("mount_inodes") or {}
        if not isinstance(inodes, dict) or not inodes:
            return None
        for pct in inodes.values():
            try:
                value = float(pct)
            except (TypeError, ValueError):
                continue
            if value > max_pct:
                return "critical" if value > 95.0 else "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="inode usage high; consider tmpfs prune or reformat")


@dataclass
class _UnpackerScratchFloor:
    id: str = "storage:unpacker_scratch_floor"
    domain: str = _DOMAIN
    description: str = (
        "Unpacker scratch must be at least 2× the largest in-flight archive "
        "or extraction will fail mid-stream."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"multiplier": 2.0}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        mult = float(threshold.get("multiplier", 2.0))
        scratch = state.get("unpacker_scratch") or {}
        if not isinstance(scratch, dict) or not scratch:
            return None
        free = float(scratch.get("free_bytes") or 0)
        biggest = float(scratch.get("largest_in_flight_bytes") or 0)
        if biggest <= 0:
            return None
        if free < biggest * mult:
            return "warning" if free >= biggest else "critical"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="unpacker scratch insufficient")


@dataclass
class _TrashRetention:
    id: str = "storage:trash_retention"
    domain: str = _DOMAIN
    description: str = (
        "Items in *arr Recycle Bins older than the configured age "
        "should be purged to reclaim disk."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_age_days": 14}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        max_days = float(threshold.get("max_age_days", 14))
        bins = state.get("arr_recycle_bins") or []
        if not isinstance(bins, list):
            return None
        for entry in bins:
            if not isinstance(entry, dict):
                continue
            age = float(entry.get("age_days") or 0)
            count = int(entry.get("count") or 0)
            if age > max_days and count > 0:
                return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="purge stale recycle-bin entries")


@dataclass
class _SnapshotRetention:
    id: str = "storage:snapshot_retention"
    domain: str = _DOMAIN
    description: str = (
        "Too many snapshots, or snapshots older than the retention "
        "horizon, block disk reclaim and slow restore-on-corrupt."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_count": 50, "max_age_days": 30}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        max_count = int(threshold.get("max_count", 50))
        max_age = float(threshold.get("max_age_days", 30))
        snaps = state.get("snapshots") or {}
        if not isinstance(snaps, dict) or not snaps:
            return None
        count = int(snaps.get("count") or 0)
        oldest = float(snaps.get("oldest_age_days") or 0)
        if count > max_count or oldest > max_age:
            return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="prune snapshots older than retention horizon")


# Side-effect register every rule on import.
register_guardrail(_PerMountThreshold())
register_guardrail(_FreeSpaceFloor())
register_guardrail(_PerContentTypeQuota())
register_guardrail(_InodeFloor())
register_guardrail(_UnpackerScratchFloor())
register_guardrail(_TrashRetention())
register_guardrail(_SnapshotRetention())
