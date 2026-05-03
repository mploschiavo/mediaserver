"""Registry loader for promise YAML files.

ADR-0006 Phase 1 reshaped this module from a loose-function shim
into a small class hierarchy that the orchestrator and tests can
configure independently. Two source locations are supported:

  1. ``contracts/services/<svc>.yaml::plugin.promises:`` — per-service
     promises co-located with the service's job + lifecycle
     declarations (Phase 2 of ADR-0006 migrates entries here, family
     by family).
  2. ``contracts/promises/cross_cutting.yaml::promises:`` AND
     ``contracts/promises/promises.yaml::promises:`` — cross-cutting
     promises (gateway, audit, infra) plus the legacy monolith.
     Phase 3 retires the legacy filename.

The aggregating loader reads both sources, validates cross-file
invariants (id uniqueness, ``depends_on`` resolution,
``LifecycleEnsurer`` / ``JobEnsurer`` reference resolution), and
emits a single flat list of typed :class:`Promise` values for the
orchestrator to dispatch against.

Design (named patterns):

  * **Strategy + Dispatch Table** for probe and ensurer parsers —
    one class per parser type, each holding a name -> builder-method
    dispatch dict so a new probe kind is one method addition rather
    than a deep if/elif edit.
  * **Composition** for :class:`PromiseEntryParser` — wires a
    :class:`ProbeSpecParser` and an :class:`EnsurerSpecParser` via
    constructor injection so tests can swap in fakes for either
    independently.
  * **Repository / Aggregator** for :class:`PromiseRegistryLoader`
    — exposes ``load_per_service()`` / ``load_cross_cutting()`` /
    ``aggregate(...)`` returning a :class:`PromiseRegistryResult`
    that bundles the validated promise list with the source-file
    map (so error messages can point operators at the right file).

Module-level shims (``default_registry_path``,
``default_contracts_root``, ``load_registry``) preserve the
backwards-compatible function-call surface for downstream callers
(``application/services/orchestrator.py``,
``infrastructure/promises/dispatcher.py``, the ratchet tests).
They delegate to default-instantiated class instances.
"""

from __future__ import annotations

import logging
import os as _os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from media_stack.domain.services.promises import (
    DeployEnsurer,
    EnsurerSpec,
    FileJsonProbe,
    FileTextProbe,
    HttpJsonProbe,
    HttpStatusProbe,
    HttpTextProbe,
    InfraEnsurer,
    JobEnsurer,
    K8sExecProbe,
    K8sResourceProbe,
    LifecycleEnsurer,
    LifecycleProbe,
    ProbeSpec,
    Promise,
    PromiseRegistryError,
)


logger = logging.getLogger(__name__)


_INFRA_VOCABULARY = frozenset({
    "kubectl-apply",
    "operator",
    "seed-runtime-overrides",
})


class ContractsLocator:
    """Resolves the project's ``contracts/`` directory.

    Constructor-injects an environment provider + path-existence
    predicates so tests can drive the candidate-walk logic without
    touching the real filesystem or process env.
    """

    _ENV_OVERRIDE = "MEDIA_STACK_CONTRACTS_ROOT"

    def __init__(
        self,
        *,
        env_provider: Mapping[str, str] | None = None,
        is_dir: Callable[[Path], bool] | None = None,
        is_file: Callable[[Path], bool] | None = None,
        package_path: Path | None = None,
    ) -> None:
        self._env: Mapping[str, str] = (
            env_provider if env_provider is not None else _os.environ
        )
        self._is_dir = is_dir if is_dir is not None else Path.is_dir
        self._is_file = is_file if is_file is not None else Path.is_file
        self._package_path = (
            package_path if package_path is not None
            else Path(__file__).resolve().parents[4]
        )

    def root(self) -> Path:
        explicit = self._env_override()
        if explicit is not None:
            return explicit
        for candidate in self._candidate_roots():
            if self._is_dir(candidate / "services") or self._is_dir(
                candidate / "promises",
            ):
                return candidate
        return self._candidate_roots()[0]

    def legacy_promises_yaml(self) -> Path:
        explicit = self._env_override()
        if explicit is not None:
            return explicit / "promises" / "promises.yaml"
        for candidate in self._candidate_roots():
            target = candidate / "promises" / "promises.yaml"
            if self._is_file(target):
                return target
        return self._candidate_roots()[0] / "promises" / "promises.yaml"

    def cross_cutting_yaml(self) -> Path:
        root = self.root()
        cross = root / "promises" / "cross_cutting.yaml"
        if self._is_file(cross):
            return cross
        return root / "promises" / "promises.yaml"

    def per_service_yamls(self) -> list[Path]:
        services_dir = self.root() / "services"
        if not self._is_dir(services_dir):
            return []
        return sorted(services_dir.glob("*.yaml"))

    def _env_override(self) -> Path | None:
        explicit = (self._env.get(self._ENV_OVERRIDE) or "").strip()
        return Path(explicit) if explicit else None

    def _candidate_roots(self) -> list[Path]:
        return [
            self._package_path / "contracts",
            Path("/app/contracts"),
            Path("/contracts"),
            Path("/usr/local/share/media-stack/contracts"),
            Path("/opt/media-stack/contracts"),
        ]


class ProbeSpecParser:
    """Parses YAML dicts into ``ProbeSpec`` discriminated-union
    values.

    Strategy + Dispatch Table — name -> bound-method dispatch dict
    so adding a new probe kind means one ``_build_<kind>`` instance
    method and one dispatch entry, not an if/elif edit threaded
    through unrelated branches."""

    def __init__(self) -> None:
        self._dispatch: dict[str, Callable[[str, Mapping[str, Any]], ProbeSpec]] = {
            "lifecycle": self._build_lifecycle,
            "http_json": self._build_http_json,
            "http_text": self._build_http_text,
            "http_status": self._build_http_status,
            "file_json": self._build_file_json,
            "file_text": self._build_file_text,
            "k8s_resource": self._build_k8s_resource,
            "k8s_exec": self._build_k8s_exec,
        }

    def parse(self, pid: str, probe_raw: Any) -> ProbeSpec:
        if not isinstance(probe_raw, dict):
            raise PromiseRegistryError(f"{pid}: ``probe`` must be a dict")
        ptype = str(probe_raw.get("type") or "").strip()
        if not ptype:
            raise PromiseRegistryError(f"{pid}: probe missing ``type`` field")
        builder = self._dispatch.get(ptype)
        if builder is None:
            raise PromiseRegistryError(
                f"{pid}: unknown probe type {ptype!r}; expected one of "
                f"{sorted(self._dispatch.keys())}",
            )
        return builder(pid, probe_raw)

    def _build_lifecycle(self, pid: str, raw: Mapping[str, Any]) -> ProbeSpec:
        return LifecycleProbe(
            service=str(raw.get("service") or "").strip(),
            method=str(raw.get("method") or "").strip(),
        )

    def _build_http_json(self, pid: str, raw: Mapping[str, Any]) -> ProbeSpec:
        return HttpJsonProbe(
            service=str(raw.get("service") or "").strip(),
            path=str(raw.get("path") or ""),
            auth=str(raw.get("auth") or "none"),
            assert_expr=str(raw.get("assert") or ""),
            sni=str(raw.get("sni") or "").strip(),
        )

    def _build_http_text(self, pid: str, raw: Mapping[str, Any]) -> ProbeSpec:
        return HttpTextProbe(
            service=str(raw.get("service") or "").strip(),
            path=str(raw.get("path") or ""),
            auth=str(raw.get("auth") or "none"),
            assert_expr=str(raw.get("assert") or ""),
            sni=str(raw.get("sni") or "").strip(),
        )

    def _build_http_status(self, pid: str, raw: Mapping[str, Any]) -> ProbeSpec:
        return HttpStatusProbe(
            service=str(raw.get("service") or "").strip(),
            path=str(raw.get("path") or ""),
            auth=str(raw.get("auth") or "none"),
            assert_expr=str(raw.get("assert") or ""),
            sni=str(raw.get("sni") or "").strip(),
        )

    def _build_file_json(self, pid: str, raw: Mapping[str, Any]) -> ProbeSpec:
        return FileJsonProbe(
            path=str(raw.get("path") or ""),
            assert_expr=str(raw.get("assert") or ""),
            skip_if_missing=bool(raw.get("skip_if_missing", False)),
        )

    def _build_file_text(self, pid: str, raw: Mapping[str, Any]) -> ProbeSpec:
        return FileTextProbe(
            path=str(raw.get("path") or ""),
            assert_expr=str(raw.get("assert") or ""),
            skip_if_missing=bool(raw.get("skip_if_missing", False)),
        )

    def _build_k8s_resource(self, pid: str, raw: Mapping[str, Any]) -> ProbeSpec:
        return K8sResourceProbe(
            resource_kind=str(raw.get("kind") or "").strip(),
            namespace=str(raw.get("namespace") or "").strip(),
            label_selector=str(raw.get("label_selector") or "").strip(),
            assert_expr=str(raw.get("assert") or ""),
        )

    def _build_k8s_exec(self, pid: str, raw: Mapping[str, Any]) -> ProbeSpec:
        cmd = raw.get("command") or ()
        if not isinstance(cmd, (list, tuple)):
            raise PromiseRegistryError(
                f"{pid}: k8s_exec ``command`` must be a list",
            )
        return K8sExecProbe(
            namespace=str(raw.get("namespace") or "").strip(),
            pod_label=str(raw.get("pod_label") or "").strip(),
            container=str(raw.get("container") or "").strip(),
            command=tuple(str(x) for x in cmd),
            assert_expr=str(raw.get("assert") or ""),
            skip_if_unset=str(raw.get("skip_if_unset") or ""),
        )


class EnsurerSpecParser:
    """Parses ``ensured_by`` into ``EnsurerSpec`` values.

    Legacy schema uses a bare string (infra-vocabulary token or
    contract-job name); new schema uses a typed dict. Both
    round-trip through the same parser; downstream code only sees
    the typed value."""

    def __init__(
        self,
        *,
        infra_vocabulary: frozenset[str] = _INFRA_VOCABULARY,
    ) -> None:
        self._infra_vocabulary = infra_vocabulary
        self._typed_dispatch: dict[
            str, Callable[[str, Mapping[str, Any]], EnsurerSpec]
        ] = {
            "lifecycle": self._build_lifecycle,
            "job": self._build_job,
            "deploy": self._build_deploy,
            "infra": self._build_infra,
        }

    def parse(self, pid: str, ensurer_raw: Any) -> EnsurerSpec:
        if isinstance(ensurer_raw, str):
            return self._parse_bare_string(pid, ensurer_raw)
        if isinstance(ensurer_raw, dict):
            return self._parse_typed_dict(pid, ensurer_raw)
        raise PromiseRegistryError(
            f"{pid}: ``ensured_by`` must be a string or dict (got "
            f"{type(ensurer_raw).__name__})",
        )

    def _parse_bare_string(self, pid: str, raw: str) -> EnsurerSpec:
        s = raw.strip()
        if not s:
            raise PromiseRegistryError(f"{pid}: ``ensured_by`` is empty")
        if s in self._infra_vocabulary:
            return InfraEnsurer(operator=s)
        return JobEnsurer(job_name=s)

    def _parse_typed_dict(
        self, pid: str, raw: Mapping[str, Any],
    ) -> EnsurerSpec:
        etype = str(raw.get("type") or "").strip()
        builder = self._typed_dispatch.get(etype)
        if builder is None:
            raise PromiseRegistryError(
                f"{pid}: unknown ``ensured_by.type`` {etype!r}; expected "
                f"{sorted(self._typed_dispatch.keys())}",
            )
        return builder(pid, raw)

    def _build_lifecycle(self, pid: str, raw: Mapping[str, Any]) -> EnsurerSpec:
        return LifecycleEnsurer(
            service=str(raw.get("service") or "").strip(),
            method=str(raw.get("method") or "").strip(),
        )

    def _build_job(self, pid: str, raw: Mapping[str, Any]) -> EnsurerSpec:
        return JobEnsurer(
            job_name=str(raw.get("job_name") or "").strip(),
        )

    def _build_deploy(self, pid: str, raw: Mapping[str, Any]) -> EnsurerSpec:
        return DeployEnsurer(
            target=str(raw.get("target") or "").strip(),
        )

    def _build_infra(self, pid: str, raw: Mapping[str, Any]) -> EnsurerSpec:
        return InfraEnsurer(
            operator=str(raw.get("operator") or "").strip(),
        )


class PromiseEntryParser:
    """Parses one YAML promise entry into a typed Promise.

    Composition over inheritance — wires a probe parser and an
    ensurer parser via ctor injection so tests can swap either."""

    def __init__(
        self,
        *,
        probe_parser: ProbeSpecParser | None = None,
        ensurer_parser: EnsurerSpecParser | None = None,
    ) -> None:
        self._probe_parser = probe_parser or ProbeSpecParser()
        self._ensurer_parser = ensurer_parser or EnsurerSpecParser()

    def parse(self, entry: Mapping[str, Any]) -> Promise:
        pid = str(entry.get("id") or "").strip()
        if not pid:
            raise PromiseRegistryError(
                "promise missing required ``id`` field",
            )

        description = str(entry.get("description") or "")

        platforms_raw = entry.get("platforms")
        if not isinstance(platforms_raw, list) or not platforms_raw:
            raise PromiseRegistryError(
                f"{pid}: ``platforms`` must be a non-empty list",
            )
        platforms = tuple(
            str(x).strip() for x in platforms_raw if str(x).strip()
        )

        depends_on_raw = entry.get("depends_on") or []
        if not isinstance(depends_on_raw, list):
            raise PromiseRegistryError(
                f"{pid}: ``depends_on`` must be a list (got "
                f"{type(depends_on_raw).__name__})",
            )
        depends_on = tuple(
            str(x).strip() for x in depends_on_raw if str(x).strip()
        )

        bootstrap_blocking_raw = entry.get("bootstrap_blocking", True)
        if not isinstance(bootstrap_blocking_raw, bool):
            raise PromiseRegistryError(
                f"{pid}: ``bootstrap_blocking`` must be a bool (got "
                f"{type(bootstrap_blocking_raw).__name__})",
            )

        return Promise(
            id=pid,
            description=description,
            platforms=platforms,
            probe=self._probe_parser.parse(pid, entry.get("probe")),
            ensurer=self._ensurer_parser.parse(pid, entry.get("ensured_by")),
            depends_on=depends_on,
            bootstrap_blocking=bootstrap_blocking_raw,
        )


@dataclass(frozen=True)
class PromiseRegistryResult:
    """Validated outcome of a registry load.

    ``promises``     — flat list of typed Promise values (deduped
                       by id, in load order).
    ``source_paths`` — promise-id -> source-file path map. Operator
                       diagnostics use this to report "where did
                       this promise come from?" without forcing the
                       loader to mutate the domain dataclass.
    ``warnings``     — non-fatal diagnostics. Cross-file ensurer
                       references that LOOK suspicious but resolve
                       anywhere in the registry land here. Fatal
                       problems raise ``PromiseRegistryError``."""

    promises: tuple[Promise, ...]
    source_paths: Mapping[str, Path] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def by_id(self) -> dict[str, Promise]:
        return {p.id: p for p in self.promises}


@dataclass(frozen=True)
class _LoadedEntry:
    """Internal carrier — pairs a parsed Promise with the file it
    came from so the loader can produce file-anchored error
    messages and the public source-path map."""

    promise: Promise
    source_path: Path


class PromiseRegistryLoader:
    """Loads + validates promises from per-service contracts and
    cross-cutting registries.

    Repository / Aggregator pattern — exposes per-source loaders
    plus an ``aggregate()`` that runs cross-file validation. Real
    callers go through ``aggregate()``; the module-level
    ``load_registry`` shim is its thin wrapper for backwards
    compat."""

    def __init__(
        self,
        *,
        locator: ContractsLocator | None = None,
        entry_parser: PromiseEntryParser | None = None,
        yaml_reader: Callable[[Path], Any] | None = None,
    ) -> None:
        self._locator = locator or ContractsLocator()
        self._entry_parser = entry_parser or PromiseEntryParser()
        self._yaml_reader = yaml_reader or self._default_yaml_reader

    def aggregate(
        self,
        *,
        per_service: bool = True,
        cross_cutting: bool = True,
    ) -> PromiseRegistryResult:
        """Aggregate from both sources and validate cross-file
        invariants. Returns a frozen ``PromiseRegistryResult``."""
        per_service_entries: list[_LoadedEntry] = (
            list(self._iter_per_service()) if per_service else []
        )
        cross_cutting_entries: list[_LoadedEntry] = (
            list(self._iter_cross_cutting()) if cross_cutting else []
        )

        all_entries = per_service_entries + cross_cutting_entries
        self._reject_duplicate_ids(all_entries)

        promises = tuple(e.promise for e in all_entries)
        source_paths = {e.promise.id: e.source_path for e in all_entries}

        self._validate_depends_on(promises)
        warnings = self._collect_warnings(promises, source_paths)

        return PromiseRegistryResult(
            promises=promises,
            source_paths=source_paths,
            warnings=warnings,
        )

    def load_per_service(self) -> list[Promise]:
        return [e.promise for e in self._iter_per_service()]

    def load_cross_cutting(self) -> list[Promise]:
        return [e.promise for e in self._iter_cross_cutting()]

    def _iter_per_service(self) -> Iterable[_LoadedEntry]:
        for path in self._locator.per_service_yamls():
            doc = self._yaml_reader(path)
            if not isinstance(doc, dict):
                continue
            plugin = doc.get("plugin")
            if not isinstance(plugin, dict):
                continue
            entries = plugin.get("promises")
            if entries is None:
                continue
            yield from self._parse_entries(entries, source=path)

    def _iter_cross_cutting(self) -> Iterable[_LoadedEntry]:
        path = self._locator.cross_cutting_yaml()
        if not path.is_file():
            logger.warning(
                "promise registry missing at %s; treating as empty", path,
            )
            return
        doc = self._yaml_reader(path)
        if not isinstance(doc, dict):
            raise PromiseRegistryError(
                f"top-level YAML at {path} must be a dict, got "
                f"{type(doc).__name__}",
            )
        entries = doc.get("promises")
        if entries is None:
            return
        yield from self._parse_entries(entries, source=path)

    def _parse_entries(
        self, entries: Any, *, source: Path,
    ) -> Iterable[_LoadedEntry]:
        if not isinstance(entries, list):
            raise PromiseRegistryError(
                f"{source}: ``promises:`` must be a list",
            )
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise PromiseRegistryError(
                    f"{source}: promise entry #{idx} is not a dict "
                    f"(got {type(entry).__name__})",
                )
            yield _LoadedEntry(
                promise=self._entry_parser.parse(entry),
                source_path=source,
            )

    def _reject_duplicate_ids(
        self, entries: list[_LoadedEntry],
    ) -> None:
        seen: dict[str, Path] = {}
        for entry in entries:
            pid = entry.promise.id
            if pid in seen:
                raise PromiseRegistryError(
                    f"duplicate promise id {pid!r}: defined in both "
                    f"{seen[pid]} and {entry.source_path}",
                )
            seen[pid] = entry.source_path

    def _validate_depends_on(
        self, promises: Iterable[Promise],
    ) -> None:
        known_ids = {p.id for p in promises}
        for promise in promises:
            for dep in promise.depends_on:
                if dep not in known_ids:
                    raise PromiseRegistryError(
                        f"{promise.id}: depends_on references unknown "
                        f"promise {dep!r} (no entry with that id in any "
                        f"loaded contract)",
                    )

    def _collect_warnings(
        self,
        promises: Iterable[Promise],
        source_paths: Mapping[str, Path],
    ) -> tuple[str, ...]:
        # Phase 2 of ADR-0006 will add cross-file ensurer-resolution
        # warnings here once each per-service family has been
        # migrated. For now we don't have a registry of which jobs
        # live where (the dispatcher does the actual resolution),
        # so the warning slot is empty.
        return ()

    def _default_yaml_reader(self, path: Path) -> Any:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def default_registry_path() -> Path:
    """Return the legacy ``contracts/promises/promises.yaml`` path
    (or the env override). Preserved for callers that still want a
    single registry-file path."""
    return ContractsLocator().legacy_promises_yaml()


def default_contracts_root() -> Path:
    """Return the resolved ``contracts/`` directory."""
    return ContractsLocator().root()


def load_registry(path: Path | None = None) -> list[Promise]:
    """Parse the registry and return the typed promise list.

    When ``path`` is provided, ONLY that single YAML file is read
    (legacy contract — preserves test-fixture behaviour). When
    ``path`` is None, the loader aggregates from per-service
    contracts + cross-cutting / legacy promises.yaml and validates
    cross-file invariants."""
    if path is not None:
        return _load_single_yaml(path)
    return list(PromiseRegistryLoader().aggregate().promises)


def _load_single_yaml(path: Path) -> list[Promise]:
    if not path.is_file():
        logger.warning(
            "promise registry missing at %s; returning empty list", path,
        )
        return []
    import yaml
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise PromiseRegistryError(
            f"top-level YAML must be a dict, got {type(raw).__name__}",
        )
    entries = raw.get("promises") or []
    if not isinstance(entries, list):
        raise PromiseRegistryError("``promises:`` must be a list")
    parser = PromiseEntryParser()
    out: list[Promise] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PromiseRegistryError(
                f"promise entry #{idx} is not a dict (got "
                f"{type(entry).__name__})",
            )
        out.append(parser.parse(entry))
    return out


_default_entry_parser = PromiseEntryParser()


def _parse_promise(entry: Mapping[str, Any]) -> Promise:
    """Backwards-compat shim around ``PromiseEntryParser``. New
    code should construct + use a parser directly."""
    return _default_entry_parser.parse(entry)


__all__ = [
    "ContractsLocator",
    "EnsurerSpecParser",
    "ProbeSpecParser",
    "PromiseEntryParser",
    "PromiseRegistryLoader",
    "PromiseRegistryResult",
    "default_contracts_root",
    "default_registry_path",
    "load_registry",
]
