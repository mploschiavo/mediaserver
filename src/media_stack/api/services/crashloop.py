"""Crashloop reason classifier.

When a service pod restart count climbs above the noise floor,
``/api/health`` previously reported a generic "unhealthy" badge
and the user had no idea *why*. This module fetches the previous
container's logs once per probe and pattern-matches against a
small library of known failure signatures, returning a
``Classification`` the dashboard renders as a tooltip and the
auto-heal job uses to decide whether it can fix the issue.

Pattern matching deliberately uses literal substring tests, not
regexes — these signatures come from real production logs and
will be added to as more failure modes are observed. Each pattern
includes:

- ``cause`` — short snake-case identifier; auto-heal keys off this.
- ``description`` — one-line human explanation for the dashboard.
- ``healable`` — whether the auto-heal job has a fix for this.
- A list of substrings (case-insensitive) found in the previous
  log. Any match wins.

The classifier is read-only and does not raise. A service with
a low restart count returns ``cause='healthy'`` with no log read
at all (cheap path); the expensive ``previous_logs`` call only
happens when restart count crosses the threshold."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from typing import Iterable

from .registry import SERVICES, ServiceDef
from .workload_inspector import (
    WorkloadInspector,
    WorkloadState,
    build_default_inspector,
)

_log = logging.getLogger("controller_api")


# Minimum restart count before we bother fetching logs and
# classifying. Below this we report "healthy" — restarts during
# ordinary boot/upgrade are normal.
_RESTART_THRESHOLD = 3


@dataclass(frozen=True)
class Classification:
    """One service's crashloop diagnosis."""

    service_id: str
    restart_count: int
    cause: str            # snake_case identifier; "healthy" when fine
    description: str      # one-line human-readable
    healable: bool        # auto-heal has a fix for this cause
    sample_log_line: str  # the matching line from previous logs
    last_terminated_reason: str  # OOMKilled / Error / "" / ...
    checked_at: float

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------
# Signature library
#
# Each signature: (cause, description, healable, [substrings])
# Order matters — first match wins, so put more-specific signatures
# above generic ones.
# ----------------------------------------------------------------------


_SIGNATURES: list[tuple[str, str, bool, list[str]]] = [
    # ------------------------------------------------------------------
    # Authelia-specific signatures.
    #
    # These come up when an Authelia upgrade tightens validation for
    # a config shape we used to get away with. The 2026-04-20 outage
    # was the cookie-domain rule landing in 4.38; encryption-key
    # mismatches happen when storage.encryption_key is rotated by a
    # regen without preserving the original (see
    # AutheliaConfigGenerator._reuse_existing_secrets).
    # ------------------------------------------------------------------
    (
        "authelia_cookie_domain_invalid",
        "Authelia rejected the session cookie domain — it must contain a period or be an IP",
        True,
        ["is not a valid cookie domain",
         "must have at least a single period"],
    ),
    (
        "authelia_storage_key_rotated",
        "Authelia's storage.encryption_key changed — existing rows in db.sqlite3 can no longer be decrypted",
        False,
        ["configured encryption key does not appear to be valid for this database"],
    ),
    (
        "authelia_config_invalid",
        "Authelia rejected its configuration file (semantic validation)",
        True,
        ["can't continue due to the errors loading the configuration",
         "configuration: "],
    ),
    (
        "config_xml_corrupt",
        "Config XML file is corrupt — has trailing data after the closing tag",
        True,
        ["extra content at the end of the document"],
    ),
    (
        "config_yaml_corrupt",
        "Config YAML file is corrupt — parser rejected it",
        True,
        ["yaml.scannererror", "yaml.parsererror",
         "expected <block end>", "could not find expected ':'",
         "found unexpected end of stream"],
    ),
    (
        "config_json_corrupt",
        "Config JSON file is corrupt — parser rejected it",
        True,
        ["unexpected token", "unexpected end of json", "json.decoder.jsondecodeerror"],
    ),
    (
        "database_locked",
        "SQLite database is locked — another writer holds the file",
        False,
        ["database is locked", "sqlite_busy"],
    ),
    (
        "database_corrupt",
        "Database file is corrupt — possibly truncated by an unclean shutdown",
        True,
        ["malformed database schema", "database disk image is malformed",
         "file is not a database"],
    ),
    (
        "port_in_use",
        "Bind failed — port already in use by another process",
        False,
        ["address already in use",
         "failed to bind", "bind: address already in use"],
    ),
    (
        "perm_denied",
        "File permission error — container user can't read or write a config path",
        True,
        ["permission denied",
         "eacces", "operation not permitted"],
    ),
    (
        "missing_file",
        "Required file is missing",
        True,
        ["no such file or directory", "filenotfounderror"],
    ),
    (
        "out_of_memory",
        "Process exceeded its memory limit",
        False,
        ["killed", "out of memory", "memoryerror"],
    ),
    (
        "fatal",
        "App reported a fatal error during startup",
        False,
        ["fatal:", "panic:", "unhandled exception", "uncaughtexception"],
    ),
]


_HEALTHY = "healthy"
_UNCLASSIFIED = "unclassified"


# ----------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------


class CrashloopClassifier:

    def __init__(
        self,
        inspector: WorkloadInspector | None = None,
        services: Iterable[ServiceDef] | None = None,
        restart_threshold: int = _RESTART_THRESHOLD,
    ) -> None:
        self._inspector = inspector or build_default_inspector()
        self._services = list(services) if services is not None else list(SERVICES)
        self._threshold = restart_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_all(self) -> dict[str, dict]:
        sids = [s.id for s in self._services]
        states = self._inspector.list_workloads(sids)
        return {
            sid: self._classify(sid, states.get(sid)).to_dict()
            for sid in sids
        }

    def check_service(self, service_id: str) -> dict:
        states = self._inspector.list_workloads([service_id])
        return self._classify(service_id, states.get(service_id)).to_dict()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _classify(
        self, service_id: str, state: WorkloadState | None,
    ) -> Classification:
        now = time.time()
        if state is None:
            return Classification(
                service_id=service_id,
                restart_count=0,
                cause=_HEALTHY,
                description="no workload state available",
                healable=False,
                sample_log_line="",
                last_terminated_reason="",
                checked_at=now,
            )

        # OOMKilled is reported by the runtime, not the app log —
        # check it before bothering with logs.
        if state.last_terminated_reason == "OOMKilled":
            return Classification(
                service_id=service_id,
                restart_count=state.restart_count,
                cause="out_of_memory",
                description="Process exceeded its memory limit (OOMKilled)",
                healable=False,
                sample_log_line="",
                last_terminated_reason=state.last_terminated_reason,
                checked_at=now,
            )

        # Quiet path: not crashlooping.
        if state.restart_count < self._threshold:
            return Classification(
                service_id=service_id,
                restart_count=state.restart_count,
                cause=_HEALTHY,
                description="restart count below threshold",
                healable=False,
                sample_log_line="",
                last_terminated_reason=state.last_terminated_reason,
                checked_at=now,
            )

        # Loud path: fetch previous logs and classify.
        logs = self._inspector.previous_logs(service_id, tail_lines=200)
        if not logs:
            return Classification(
                service_id=service_id,
                restart_count=state.restart_count,
                cause=_UNCLASSIFIED,
                description=(
                    f"Restarted {state.restart_count} times — no previous "
                    "log available to diagnose."
                ),
                healable=False,
                sample_log_line="",
                last_terminated_reason=state.last_terminated_reason,
                checked_at=now,
            )

        match = _match_signatures(logs)
        if match is None:
            return Classification(
                service_id=service_id,
                restart_count=state.restart_count,
                cause=_UNCLASSIFIED,
                description=(
                    f"Restarted {state.restart_count} times — log doesn't "
                    "match any known failure pattern."
                ),
                healable=False,
                sample_log_line="",
                last_terminated_reason=state.last_terminated_reason,
                checked_at=now,
            )

        cause, description, healable, line = match
        return Classification(
            service_id=service_id,
            restart_count=state.restart_count,
            cause=cause,
            description=description,
            healable=healable,
            sample_log_line=line,
            last_terminated_reason=state.last_terminated_reason,
            checked_at=now,
        )


def _match_signatures(
    logs: str,
) -> tuple[str, str, bool, str] | None:
    """Walk signatures in order; first hit wins. Returns the matching
    log line (one only — too many would balloon the response)."""
    lower = logs.lower()
    for cause, description, healable, needles in _SIGNATURES:
        for needle in needles:
            idx = lower.find(needle)
            if idx == -1:
                continue
            line_start = lower.rfind("\n", 0, idx) + 1
            line_end = lower.find("\n", idx)
            if line_end == -1:
                line_end = len(logs)
            sample = logs[line_start:line_end].strip()
            return cause, description, healable, sample
    return None


# ----------------------------------------------------------------------
# Module-level singleton
# ----------------------------------------------------------------------


_DEFAULT: CrashloopClassifier | None = None


def _default() -> CrashloopClassifier:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CrashloopClassifier()
    return _DEFAULT


def check_all() -> dict[str, dict]:
    return _default().check_all()


def check_service(service_id: str) -> dict:
    return _default().check_service(service_id)
