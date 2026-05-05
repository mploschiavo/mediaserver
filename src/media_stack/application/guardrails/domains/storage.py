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

import time
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


@dataclass
class _LockdownThreshold:
    """ADR-0008 Phase 1 — download-client lockdown tier.

    Sits ABOVE the existing ``_PerMountThreshold`` / ``_FreeSpaceFloor``
    cleanup rules. When any monitored mount crosses
    ``lockdown_percent`` (default 75%), the rule emits an
    ``Action(action="lockdown_engage", ...)`` which the evaluation
    loop's dispatcher hands to ``DownloadLockdownService.engage()``.
    The 15-percentage-point gap to ``release_percent`` (default 60%)
    is hysteresis — prevents engage/release flapping when usage
    hovers near the line.

    The rule reads two pieces of state beyond the standard
    ``state["disk"]`` mount info:

      * ``state["_threshold:storage:lockdown_threshold"]`` — operator
        override (lockdown_percent / release_percent), merged in by
        the evaluation loop before each tick.
      * ``state["_lockdown_state"]`` — current persisted state from
        ``DownloadLockdownService.get_state()`` (populated by the
        state collector). Without this the rule can't avoid
        re-engaging-while-already-engaged or honor the manual flag.

    Manual stickiness: if ``state["_lockdown_state"]["trigger"]`` is
    ``"manual"``, the rule never fires the auto-release path even
    when disk drops below ``release_percent``. The rule signals
    "still engaged, awaiting operator" via ``"warning"`` instead.
    """

    id: str = "storage:lockdown_threshold"
    domain: str = _DOMAIN
    description: str = (
        "Engages a download-client lockdown when any monitored mount "
        "exceeds the lockdown threshold. Releases automatically when "
        "usage drops below the release threshold (hysteresis). "
        "Operator can also engage/release manually via the API."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {
            "lockdown_percent": 75.0,
            "release_percent": 60.0,
        }
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        lockdown_pct = float(threshold.get("lockdown_percent", 75.0))
        release_pct = float(threshold.get("release_percent", 60.0))
        # If the operator misconfigures these (release >= lockdown),
        # widen the gap by clamping release down — never engage and
        # immediately release on the same evaluation.
        if release_pct >= lockdown_pct:
            release_pct = max(0.0, lockdown_pct - 1.0)

        disks = state.get("disk") or {}
        if not isinstance(disks, dict) or not disks:
            return None

        lockdown_state = state.get("_lockdown_state") or {}
        if not isinstance(lockdown_state, Mapping):
            lockdown_state = {}
        # AUTO-side TTL bypass: when an operator clicks "pause
        # guardrails 1h" the state file's ``auto_check_paused_until``
        # holds an epoch in the future. Until that passes we
        # short-circuit to None so the rule never auto-engages or
        # auto-releases. Already-paused clients stay paused — release
        # is an explicit operator action even during a TTL bypass.
        paused_until = lockdown_state.get("auto_check_paused_until")
        if paused_until is not None:
            try:
                paused_until_f = float(paused_until)
            except (TypeError, ValueError):
                paused_until_f = 0.0
            if paused_until_f > time.time():
                return None
        engaged = bool(lockdown_state.get("engaged"))
        trigger = lockdown_state.get("trigger")

        any_over_lockdown = False
        any_over_release = False
        for info in disks.values():
            if not isinstance(info, dict):
                continue
            try:
                pct = float(info.get("percent_used") or 0.0)
            except (TypeError, ValueError):
                continue
            if pct > lockdown_pct:
                any_over_lockdown = True
            if pct > release_pct:
                any_over_release = True

        if not engaged:
            # Cleanly under both bars — silent.
            if any_over_lockdown:
                return "critical"
            return None

        # engaged == True from here on.
        # Manual stickiness: never auto-release; signal "still
        # engaged, awaiting operator" via warning when disk recovers,
        # critical when disk is still over the lockdown bar.
        if trigger == "manual":
            if any_over_lockdown:
                return "critical"
            return "warning"

        # Auto-engaged: release when ALL mounts dropped under the
        # release bar; otherwise stay engaged (warning).
        if not any_over_release:
            return "info"
        return "warning"

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        lockdown_pct = float(threshold.get("lockdown_percent", 75.0))
        release_pct = float(threshold.get("release_percent", 60.0))
        if release_pct >= lockdown_pct:
            release_pct = max(0.0, lockdown_pct - 1.0)

        disks = state.get("disk") or {}
        if not isinstance(disks, dict):
            disks = {}
        lockdown_state = state.get("_lockdown_state") or {}
        if not isinstance(lockdown_state, Mapping):
            lockdown_state = {}
        # Honour the same TTL bypass the evaluate() side checks —
        # remediate is called with the same state dict so the AUTO
        # action surface stays consistent (no engage/release while
        # the operator-requested pause is in effect).
        paused_until = lockdown_state.get("auto_check_paused_until")
        if paused_until is not None:
            try:
                paused_until_f = float(paused_until)
            except (TypeError, ValueError):
                paused_until_f = 0.0
            if paused_until_f > time.time():
                return None
        engaged = bool(lockdown_state.get("engaged"))
        trigger = lockdown_state.get("trigger")

        any_over_lockdown = False
        any_over_release = False
        worst_label = ""
        worst_pct = 0.0
        for label, info in disks.items():
            if not isinstance(info, dict):
                continue
            try:
                pct = float(info.get("percent_used") or 0.0)
            except (TypeError, ValueError):
                continue
            if pct > worst_pct:
                worst_pct = pct
                worst_label = str(label)
            if pct > lockdown_pct:
                any_over_lockdown = True
            if pct > release_pct:
                any_over_release = True

        if not engaged and any_over_lockdown:
            return Action(
                rule_id=self.id,
                action="lockdown_engage",
                ok=False,
                detail=(
                    f"engage lockdown — {worst_label} at "
                    f"{worst_pct:.1f}% (> {lockdown_pct:.1f}%)"
                ),
            )

        # Auto-release path: engaged via auto + every mount dropped
        # under release_percent. Manual lockdowns never reach here.
        if engaged and trigger == "auto" and not any_over_release:
            return Action(
                rule_id=self.id,
                action="lockdown_release",
                ok=False,
                detail=(
                    f"release lockdown — disk recovered "
                    f"(worst {worst_pct:.1f}% < {release_pct:.1f}%)"
                ),
            )
        return None

    def current_value(self, state: Mapping[str, Any]) -> dict[str, Any]:
        disks = state.get("disk") or {}
        lockdown_state = state.get("_lockdown_state") or {}
        out: dict[str, Any] = {
            "engaged": bool(lockdown_state.get("engaged")),
            "trigger": lockdown_state.get("trigger"),
        }
        if isinstance(disks, dict):
            out["worst_percent"] = max(
                (
                    float(info.get("percent_used") or 0.0)
                    for info in disks.values()
                    if isinstance(info, dict)
                ),
                default=0.0,
            )
        return out


# Side-effect register every rule on import.
register_guardrail(_PerMountThreshold())
register_guardrail(_FreeSpaceFloor())
register_guardrail(_PerContentTypeQuota())
register_guardrail(_InodeFloor())
register_guardrail(_UnpackerScratchFloor())
register_guardrail(_TrashRetention())
register_guardrail(_SnapshotRetention())
register_guardrail(_LockdownThreshold())
