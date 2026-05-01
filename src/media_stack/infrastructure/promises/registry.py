"""Registry loader for ``contracts/promises/promises.yaml`` —
ADR-0003 Phase 4a.

Reads the YAML file and produces a list of typed ``Promise`` values
the orchestrator can dispatch against. Errors are reported with the
offending promise id and a one-line reason — operators editing the
YAML get actionable feedback, not unexplained ``KeyError`` traces
from deep in the loader.

Two YAML shapes coexist:

  ``ensured_by: ensure-foo-job``
      → ``JobEnsurer(job_name="ensure-foo-job")``. Legacy schema —
      ~50 entries today.

  ``ensured_by: kubectl-apply | operator | seed-runtime-overrides``
      → ``InfraEnsurer(operator="kubectl-apply")``. Out-of-band
      ensurers the orchestrator records but doesn't run.

  ``ensured_by: { type: lifecycle, service: jellyfin, method: mint_api_key }``
      → ``LifecycleEnsurer(...)``. New schema introduced by ADR-0003
      Phase 4. Adds at most one entry per service+method pair.

  ``ensured_by: { type: deploy, target: jellyfin }``
      → ``DeployEnsurer(...)``. Sketched in the ADR; out of scope
      for orchestrator dispatch in Phase 4 (the loader still
      accepts it so the schema is forward-compatible).

Probe types are similarly discriminated. The loader doesn't care
which types the dispatcher (Phase 4b) actually executes — it just
parses everything into typed values.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

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


# Out-of-band ensurer vocabulary (matches the existing
# ``test_promises_registry.py`` INFRA_ENSURED_BY set). The orchestrator
# records these as externally ensured and only re-probes — they're
# not callable from controller code.
_INFRA_VOCABULARY = frozenset({
    "kubectl-apply",
    "operator",
    "seed-runtime-overrides",
})


def default_registry_path() -> Path:
    """Find ``contracts/promises/promises.yaml`` in either dev or
    container layouts.

    Dev: ``<repo>/contracts/promises/promises.yaml`` — registry.py
        sits at ``<repo>/src/media_stack/infrastructure/promises/``,
        so ``parents[4]`` is the repo root.

    Container: contracts ship at ``/app/contracts/...`` (or
        ``/contracts/...`` in some layouts). The package itself is
        installed at ``/usr/local/lib/python3.12/site-packages/...``
        where ``parents[4]`` is NOT the repo. Walk a list of likely
        roots and return the first one that holds a real file.

    Env override: ``MEDIA_STACK_CONTRACTS_ROOT`` short-circuits the
        search when the deploy puts contracts in a non-standard
        location."""
    import os as _os
    explicit = (_os.environ.get("MEDIA_STACK_CONTRACTS_ROOT") or "").strip()
    if explicit:
        return Path(explicit) / "promises" / "promises.yaml"

    candidates = [
        Path(__file__).resolve().parents[4] / "contracts",  # dev / mounted-source
        Path("/app/contracts"),                              # standard container
        Path("/contracts"),                                  # alternate container
        Path("/usr/local/share/media-stack/contracts"),      # share dir
        Path("/opt/media-stack/contracts"),                  # opt dir
    ]
    for c in candidates:
        p = c / "promises" / "promises.yaml"
        if p.is_file():
            return p
    # Fall back to the first candidate (dev path) so the missing-file
    # warning at least points somewhere meaningful.
    return candidates[0] / "promises" / "promises.yaml"


def default_contracts_root() -> Path:
    """Find the ``contracts/`` directory regardless of dev/container
    layout. Same candidate list as ``default_registry_path``; used
    by the dispatcher to read ``contracts/services/<id>.yaml``.

    Returns the first existing root, or the dev candidate as
    fallback so the not-found error path is meaningful."""
    import os as _os
    explicit = (_os.environ.get("MEDIA_STACK_CONTRACTS_ROOT") or "").strip()
    if explicit:
        return Path(explicit)
    candidates = [
        Path(__file__).resolve().parents[4] / "contracts",
        Path("/app/contracts"),
        Path("/contracts"),
        Path("/usr/local/share/media-stack/contracts"),
        Path("/opt/media-stack/contracts"),
    ]
    for c in candidates:
        if (c / "services").is_dir() or (c / "promises").is_dir():
            return c
    return candidates[0]


def load_registry(path: Path | None = None) -> list[Promise]:
    """Parse the registry YAML and return the list of typed promises.

    Raises ``PromiseRegistryError`` on a malformed entry, with the
    offending id and a one-line reason. An empty list is returned
    when the file is missing — operators can run with no promises
    without crashing.
    """
    p = path or default_registry_path()
    if not p.is_file():
        logger.warning("promise registry missing at %s; returning empty list", p)
        return []

    import yaml
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise PromiseRegistryError(
            f"top-level YAML must be a dict, got {type(raw).__name__}",
        )

    entries = raw.get("promises") or []
    if not isinstance(entries, list):
        raise PromiseRegistryError(
            "``promises:`` must be a list",
        )

    out: list[Promise] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PromiseRegistryError(
                f"promise entry #{idx} is not a dict (got {type(entry).__name__})",
            )
        out.append(_parse_promise(entry))
    return out


# ---------------------------------------------------------------------------


def _parse_promise(entry: Mapping[str, Any]) -> Promise:
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
    platforms = tuple(str(x).strip() for x in platforms_raw if str(x).strip())

    depends_on_raw = entry.get("depends_on") or []
    if not isinstance(depends_on_raw, list):
        raise PromiseRegistryError(
            f"{pid}: ``depends_on`` must be a list (got "
            f"{type(depends_on_raw).__name__})",
        )
    depends_on = tuple(str(x).strip() for x in depends_on_raw if str(x).strip())

    probe = _parse_probe(pid, entry.get("probe"))
    ensurer = _parse_ensurer(pid, entry.get("ensured_by"))

    return Promise(
        id=pid,
        description=description,
        platforms=platforms,
        probe=probe,
        ensurer=ensurer,
        depends_on=depends_on,
    )


def _parse_probe(pid: str, probe_raw: Any) -> ProbeSpec:
    if not isinstance(probe_raw, dict):
        raise PromiseRegistryError(
            f"{pid}: ``probe`` must be a dict",
        )
    ptype = str(probe_raw.get("type") or "").strip()
    if not ptype:
        raise PromiseRegistryError(
            f"{pid}: probe missing ``type`` field",
        )

    if ptype == "lifecycle":
        return LifecycleProbe(
            service=str(probe_raw.get("service") or "").strip(),
            method=str(probe_raw.get("method") or "").strip(),
        )
    if ptype == "http_json":
        return HttpJsonProbe(
            service=str(probe_raw.get("service") or "").strip(),
            path=str(probe_raw.get("path") or ""),
            auth=str(probe_raw.get("auth") or "none"),
            assert_expr=str(probe_raw.get("assert") or ""),
        )
    if ptype == "http_text":
        return HttpTextProbe(
            service=str(probe_raw.get("service") or "").strip(),
            path=str(probe_raw.get("path") or ""),
            auth=str(probe_raw.get("auth") or "none"),
            assert_expr=str(probe_raw.get("assert") or ""),
        )
    if ptype == "http_status":
        return HttpStatusProbe(
            service=str(probe_raw.get("service") or "").strip(),
            path=str(probe_raw.get("path") or ""),
            auth=str(probe_raw.get("auth") or "none"),
            assert_expr=str(probe_raw.get("assert") or ""),
        )
    if ptype == "file_json":
        return FileJsonProbe(
            path=str(probe_raw.get("path") or ""),
            assert_expr=str(probe_raw.get("assert") or ""),
            skip_if_missing=bool(probe_raw.get("skip_if_missing", False)),
        )
    if ptype == "file_text":
        return FileTextProbe(
            path=str(probe_raw.get("path") or ""),
            assert_expr=str(probe_raw.get("assert") or ""),
            skip_if_missing=bool(probe_raw.get("skip_if_missing", False)),
        )
    if ptype == "k8s_resource":
        return K8sResourceProbe(
            resource_kind=str(probe_raw.get("kind") or "").strip(),
            namespace=str(probe_raw.get("namespace") or "").strip(),
            label_selector=str(probe_raw.get("label_selector") or "").strip(),
            assert_expr=str(probe_raw.get("assert") or ""),
        )
    if ptype == "k8s_exec":
        cmd = probe_raw.get("command") or ()
        if not isinstance(cmd, (list, tuple)):
            raise PromiseRegistryError(
                f"{pid}: k8s_exec ``command`` must be a list",
            )
        return K8sExecProbe(
            namespace=str(probe_raw.get("namespace") or "").strip(),
            pod_label=str(probe_raw.get("pod_label") or "").strip(),
            container=str(probe_raw.get("container") or "").strip(),
            command=tuple(str(x) for x in cmd),
            assert_expr=str(probe_raw.get("assert") or ""),
            skip_if_unset=str(probe_raw.get("skip_if_unset") or ""),
        )

    raise PromiseRegistryError(
        f"{pid}: unknown probe type {ptype!r}; expected one of "
        "lifecycle, http_json, http_text, http_status, file_json, "
        "file_text, k8s_resource, k8s_exec",
    )


def _parse_ensurer(pid: str, ensurer_raw: Any) -> EnsurerSpec:
    # Legacy schema: bare string. Either a contract job name
    # (``ensure-foo-job``) or an infra-vocabulary token.
    if isinstance(ensurer_raw, str):
        s = ensurer_raw.strip()
        if not s:
            raise PromiseRegistryError(
                f"{pid}: ``ensured_by`` is empty",
            )
        if s in _INFRA_VOCABULARY:
            return InfraEnsurer(operator=s)
        # Heuristic: bare strings we don't recognize as infra are
        # treated as job names. The schema ratchet (Phase 4a) asserts
        # they resolve to a real contract job.
        return JobEnsurer(job_name=s)

    # New schema: typed dict.
    if isinstance(ensurer_raw, dict):
        etype = str(ensurer_raw.get("type") or "").strip()
        if etype == "lifecycle":
            return LifecycleEnsurer(
                service=str(ensurer_raw.get("service") or "").strip(),
                method=str(ensurer_raw.get("method") or "").strip(),
            )
        if etype == "job":
            return JobEnsurer(
                job_name=str(ensurer_raw.get("job_name") or "").strip(),
            )
        if etype == "deploy":
            return DeployEnsurer(
                target=str(ensurer_raw.get("target") or "").strip(),
            )
        if etype == "infra":
            return InfraEnsurer(
                operator=str(ensurer_raw.get("operator") or "").strip(),
            )
        raise PromiseRegistryError(
            f"{pid}: unknown ``ensured_by.type`` {etype!r}; expected "
            "lifecycle, job, deploy, or infra",
        )

    raise PromiseRegistryError(
        f"{pid}: ``ensured_by`` must be a string or dict (got "
        f"{type(ensurer_raw).__name__})",
    )


__all__ = ["default_registry_path", "load_registry"]
