"""Per-service config-file integrity probe.

The 2026-04-20 Prowlarr crashloop was invisible to the existing
HTTP probes — the pod kept restarting because its
``config.xml`` had trailing junk after ``</Config>``, but the
controller's ``/api/health`` only saw "no response on :9696" and
reported a generic "unhealthy". The actual cause was on disk.

This module reads each service's primary config file (path comes
from the service registry's ``api_key_config`` field) and tries
to parse it according to the declared format. The result is a
small ``IntegrityResult`` per service that other components
(dashboard, auto-heal, composite stories) consume:

- ``status`` is ``ok`` (parsed cleanly + semantic check passed),
  ``corrupt`` (parser raised), ``invalid`` (parsed but a
  registered semantic validator rejected it — e.g. Authelia
  ``cookie_domain="local"``), ``missing`` (file not present yet —
  pre-bootstrap), ``unknown`` (no config file declared —
  informational), or ``skipped`` (declared but format
  unsupported).
- ``reason`` is a one-line human description, surfaced as a
  tooltip in the dashboard and used by the composite-story
  layer to explain *what* is broken.
- ``file`` is the path actually inspected, useful for support.

The probe is read-only and intentionally never raises — the
caller is the dashboard, and a probe that hard-errors is a
worse outcome than a probe that reports "unknown" for one
service. Format-specific errors (XML ParseError, YAML
ScannerError, JSON decoder, INI ParseError) are caught and
flattened into the ``reason`` string.

Authelia's ``configuration.yml`` lives outside the per-service
registry (Authelia isn't a "service" in the registry sense — it's
the SSO provider). It is added explicitly via
``_INFRA_CONFIGS`` so the integrity probe still covers it; the
auto-heal job snapshots it like any other config and restores it
when the semantic validator rejects the live file."""

from __future__ import annotations

import configparser
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import yaml

from .config_validators import get_validator
from media_stack.core.service_registry.registry import SERVICES, ServiceDef

_log = logging.getLogger("controller_api")

# ``api_key_format`` values we know how to parse. Anything else
# yields ``status=skipped`` so the dashboard doesn't lie about
# health for a service we can't actually inspect.
_SUPPORTED_FORMATS = {"xml", "yaml", "json", "ini", "sqlite"}


# Infrastructure config files that aren't owned by a registry
# service but still benefit from integrity + auto-heal coverage.
# Each entry: (probe_id, relative_path, format).
_INFRA_CONFIGS: list[tuple[str, str, str]] = [
    ("authelia", "authelia/configuration.yml", "yaml"),
]


@dataclass(frozen=True)
class IntegrityResult:
    """One probe outcome. Frozen so it can be cached safely."""

    service_id: str
    status: str            # ok | corrupt | missing | unknown | skipped
    file: str              # absolute path inspected, or "" if N/A
    format: str            # xml | yaml | json | ini | sqlite | ""
    reason: str            # one-line diagnostic; empty when ok
    checked_at: float      # epoch seconds

    def to_dict(self) -> dict:
        return asdict(self)


class _ProbeError(Exception):
    """Internal: format-specific parsers raise this with a
    one-line human-readable message. Never propagated."""


class ConfigIntegrityProber:
    """Format-specific parse probes. One instance is reused for
    every file inspected. The probes are intentionally narrow —
    they only assert "the parser accepts this byte stream"; any
    semantic validation lives in :mod:`.config_validators`."""

    def probe(self, fmt: str, path: Path) -> Any:
        """Dispatch on ``fmt`` to the matching probe method.
        Raises :class:`_ProbeError` on any parse / IO failure."""
        if fmt == "xml":
            return self._probe_xml(path)
        if fmt == "yaml":
            return self._probe_yaml(path)
        if fmt == "json":
            return self._probe_json(path)
        if fmt == "ini":
            return self._probe_ini(path)
        if fmt == "sqlite":
            return self._probe_sqlite(path)
        raise _ProbeError(f"unsupported format '{fmt}'")

    def _probe_xml(self, path: Path) -> ET.Element:
        try:
            return ET.fromstring(path.read_bytes())
        except ET.ParseError as exc:
            raise _ProbeError(f"XML parse error: {exc}") from exc
        except OSError as exc:
            raise _ProbeError(f"unreadable: {exc}") from exc

    def _probe_yaml(self, path: Path) -> Any:
        try:
            with path.open("rb") as fh:
                return yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            # PyYAML messages span multiple lines; flatten to one.
            raise _ProbeError(
                "YAML parse error: " + " ".join(str(exc).split())
            ) from exc
        except OSError as exc:
            raise _ProbeError(f"unreadable: {exc}") from exc

    def _probe_json(self, path: Path) -> Any:
        try:
            with path.open("rb") as fh:
                return json.load(fh)
        except json.JSONDecodeError as exc:
            raise _ProbeError(f"JSON parse error: {exc}") from exc
        except OSError as exc:
            raise _ProbeError(f"unreadable: {exc}") from exc

    def _probe_ini(self, path: Path) -> configparser.ConfigParser:
        cp = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            # SABnzbd's sabnzbd.ini contains values with bare ``%`` —
            # turning interpolation off avoids InterpolationSyntaxError
            # for what is otherwise a perfectly valid file. SABnzbd
            # also writes a preamble line (``__version__ = 19``) BEFORE
            # any ``[section]`` header, which stdlib configparser
            # rejects with "File contains no section headers". Inject a
            # synthetic ``[__top__]`` section so the preamble parses;
            # the real ``[misc]``/``[servers]``/etc sections that follow
            # parse normally.
            raw = path.read_text(encoding="utf-8")
            if not self._has_section_header(raw):
                text = "[__top__]\n" + raw
            else:
                text = raw
            cp.read_string(text)
        except configparser.Error as exc:
            raise _ProbeError(f"INI parse error: {exc}") from exc
        except OSError as exc:
            raise _ProbeError(f"unreadable: {exc}") from exc
        return cp

    def _has_section_header(self, text: str) -> bool:
        """Returns True iff the file contains at least one
        ``[section]`` header before its first key=value line. SABnzbd
        writes a few bare top-level keys before the first section; if
        we wrap the whole file in a synthetic section we'd hide a
        really truncated/corrupt file. So: only synthesize when the
        very first non-blank, non-comment line is a key=value, never
        when a section header is genuinely missing from a structured
        file."""
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith(("#", ";")):
                continue
            # First content line: section header means the file is
            # well-formed already; key=value means we're in
            # SABnzbd-style preamble territory.
            return s.startswith("[") and "]" in s
        return True  # empty file — no synthesis needed

    def _probe_sqlite(self, path: Path) -> None:
        """Open in read-only mode and run a schema query. We only care
        that the DB header is well-formed and the file isn't truncated;
        we deliberately don't validate any specific schema."""
        try:
            uri = f"file:{path}?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
            try:
                con.execute("SELECT 1 FROM sqlite_master LIMIT 1")
            finally:
                con.close()
        except sqlite3.DatabaseError as exc:
            raise _ProbeError(f"SQLite error: {exc}") from exc
        except OSError as exc:
            raise _ProbeError(f"unreadable: {exc}") from exc


class ConfigIntegrityService:
    """Reads per-service config files from disk and reports
    parseability. Stateless — a singleton instance is fine, but
    callers can construct their own with a fake ``config_root``
    in tests."""

    def __init__(
        self,
        config_root: str | os.PathLike | None = None,
        services: Iterable[ServiceDef] | None = None,
        prober: ConfigIntegrityProber | None = None,
    ) -> None:
        self._config_root = Path(
            config_root
            if config_root is not None
            else os.environ.get("CONFIG_ROOT", "/srv-config")
        )
        self._services = list(services) if services is not None else list(SERVICES)
        self._prober = prober if prober is not None else ConfigIntegrityProber()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_all(self) -> dict[str, dict]:
        """Probe every service that declares an ``api_key_config``,
        plus infrastructure configs (Authelia) that aren't in the
        service registry. Returns a dict keyed by service id with
        serialised results.

        Services without a declared config file are reported with
        ``status=unknown`` so the dashboard can render a neutral
        badge — the probe doesn't pretend to know."""
        out: dict[str, dict] = {}
        for svc in self._services:
            out[svc.id] = self.check_service(svc.id).to_dict()
        for probe_id, rel_path, fmt in _INFRA_CONFIGS:
            if probe_id in out:
                # Don't overwrite a registry-driven entry if one
                # exists with the same id.
                continue
            out[probe_id] = self._probe_path(
                probe_id, rel_path, fmt,
            ).to_dict()
        return out

    def check_service(self, service_id: str) -> IntegrityResult:
        svc = self._lookup(service_id)
        if svc is None:
            return IntegrityResult(
                service_id=service_id,
                status="unknown",
                file="",
                format="",
                reason=f"unknown service '{service_id}'",
                checked_at=time.time(),
            )
        if not svc.api_key_config:
            return IntegrityResult(
                service_id=service_id,
                status="unknown",
                file="",
                format="",
                reason="no config file declared in service registry",
                checked_at=time.time(),
            )
        return self._probe(svc)

    def check_service_dict(self, service_id: str) -> dict:
        """Dict-returning shape for callers that serialise the
        result directly (the GET handler import path expects this
        shape)."""
        return self.check_service(service_id).to_dict()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _lookup(self, service_id: str) -> ServiceDef | None:
        for svc in self._services:
            if svc.id == service_id:
                return svc
        return None

    def _probe(self, svc: ServiceDef) -> IntegrityResult:
        return self._probe_path(svc.id, svc.api_key_config, svc.api_key_format)

    def _probe_path(
        self, probe_id: str, rel_path: str, fmt: str,
    ) -> IntegrityResult:
        path = self._config_root / rel_path
        fmt = (fmt or "").lower()
        now = time.time()
        if fmt not in _SUPPORTED_FORMATS:
            return IntegrityResult(
                service_id=probe_id,
                status="skipped",
                file=str(path),
                format=fmt,
                reason=f"format '{fmt or 'unset'}' not validated",
                checked_at=now,
            )
        if not path.exists():
            # Distinguish "service isn't deployed at all" (parent
            # config dir doesn't exist — no PVC bound, no compose
            # container) from "service is deployed but hasn't
            # written its config yet" (pre-bootstrap). The former
            # is a false positive for the dashboard's broken-count;
            # the latter is real signal worth surfacing.
            if not path.parent.exists():
                return IntegrityResult(
                    service_id=probe_id,
                    status="not_deployed",
                    file=str(path),
                    format=fmt,
                    reason=(
                        "service not deployed on this platform — "
                        "config root has no directory for it"
                    ),
                    checked_at=now,
                )
            return IntegrityResult(
                service_id=probe_id,
                status="missing",
                file=str(path),
                format=fmt,
                reason="file not present yet (pre-bootstrap?)",
                checked_at=now,
            )

        try:
            parsed = self._prober.probe(fmt, path)
        except _ProbeError as exc:
            return IntegrityResult(
                service_id=probe_id,
                status="corrupt",
                file=str(path),
                format=fmt,
                reason=str(exc),
                checked_at=now,
            )

        # Semantic validation. Parsing succeeded; if a registered
        # validator rejects the parsed structure (e.g. Authelia's
        # bare ``cookie_domain="local"``), report status=invalid
        # so the dashboard can distinguish "syntactically corrupt"
        # from "syntactically fine but the app will reject it".
        validator = get_validator(probe_id)
        if validator is not None and parsed is not None:
            try:
                errors = validator(parsed)
            except Exception as exc:  # noqa: BLE001
                _log.debug(
                    "[DEBUG] semantic validator for %s raised: %s",
                    probe_id, exc,
                )
                errors = []
            if errors:
                first = errors[0]
                extra = (f" (+{len(errors) - 1} more)"
                         if len(errors) > 1 else "")
                return IntegrityResult(
                    service_id=probe_id,
                    status="invalid",
                    file=str(path),
                    format=fmt,
                    reason=f"{first.rule}: {first.message}{extra}",
                    checked_at=now,
                )

        return IntegrityResult(
            service_id=probe_id,
            status="ok",
            file=str(path),
            format=fmt,
            reason="",
            checked_at=now,
        )


# ----------------------------------------------------------------------
# Module-level singleton for the GET handler import path.
# ----------------------------------------------------------------------


_DEFAULT = ConfigIntegrityService()


# Thin aliases preserved for backwards compatibility. New code
# should construct a :class:`ConfigIntegrityService` instance and
# call its methods directly.
check_all = _DEFAULT.check_all
check_service = _DEFAULT.check_service_dict
