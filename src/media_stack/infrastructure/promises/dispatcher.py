"""Probe + ensurer dispatch tables — ADR-0003 Phase 4b.

The orchestrator pattern-matches on ``ProbeSpec.kind`` /
``EnsurerSpec.kind`` to invoke the right handler. This module hosts
the dispatch tables and the lifecycle-class resolver they share.

Why infrastructure: probes do real I/O (HTTP, file reads) and
ensurers can mutate state. ``domain/services/`` would be polluted;
``application/services/`` is where the orchestrator lives but the
dispatcher is a leaf utility, not orchestration logic.

The lifecycle resolver caches resolved instances per process —
``JellyfinLifecycle()`` is stateless, no point re-importing on every
tick.

Assert expressions in YAML are evaluated by ``probe_promises._evaluate``
(reused, not re-implemented — that helper has the multi-line +
scope-handling treatment for ``all(...)`` / ``any(...)`` Python
gotchas, and centralizes the sandboxed eval in one auditable place).
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

import yaml

from media_stack.domain.services.lifecycle import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ServiceLifecycle,
)
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
)


logger = logging.getLogger(__name__)


_PROBE_TIMEOUT_SECONDS = 5.0


# Reused YAML-assert evaluator — single auditable eval site.
def _eval_assert(expr: str, scope: Mapping[str, Any]) -> tuple[bool, str]:
    from media_stack.cli.commands.probe_promises import _evaluate
    return _evaluate(expr, dict(scope))


# ============================================================================
# Lifecycle class resolver — shared between probe + ensurer dispatch
# ============================================================================


class LifecycleResolver:
    """Looks up a service's lifecycle class from its contract YAML
    and instantiates it (caching the instance for the process
    lifetime — instances are stateless).

    The orchestrator passes one resolver into both the probe and
    ensurer dispatch tables so a single per-tick lookup serves both
    questions ("can I probe X?" and "can I mint X?").
    """

    def __init__(self, contracts_dir: Path | None = None) -> None:
        self._contracts_dir = contracts_dir or _default_contracts_dir()
        self._lock = threading.Lock()
        self._instance_cache: dict[str, ServiceLifecycle] = {}
        self._dotted_cache: dict[str, str] = {}
        self._config_cache: dict[str, dict[str, Any]] = {}

    def resolve(self, service_id: str) -> ServiceLifecycle | None:
        """Return the cached lifecycle instance for ``service_id`` or
        ``None`` if the service has no lifecycle class declared (or
        the class can't be resolved). Logs at ERROR on misses; the
        orchestrator decides whether a missing lifecycle is fatal."""
        with self._lock:
            cached = self._instance_cache.get(service_id)
            if cached is not None:
                return cached
            dotted = self._dotted_cache.get(service_id)
            if dotted is None:
                dotted = self._read_lifecycle_class(service_id)
                self._dotted_cache[service_id] = dotted
            if not dotted:
                return None
            try:
                instance = _instantiate(dotted, service_id)
            except (ImportError, AttributeError, ValueError) as exc:
                logger.error(
                    "lifecycle_class %r for service %r unresolvable: %s",
                    dotted, service_id, exc,
                )
                return None
            if not isinstance(instance, ServiceLifecycle):
                logger.error(
                    "lifecycle_class %r for service %r does not satisfy "
                    "ServiceLifecycle Protocol", dotted, service_id,
                )
                return None
            self._instance_cache[service_id] = instance
            return instance

    def context_for(
        self,
        service_id: str,
        *,
        secrets: Mapping[str, str] | None = None,
        now_fn: Any = None,
    ) -> OrchestrationContext:
        cfg = self.read_service_config(service_id)
        return OrchestrationContext(
            service_id=service_id,
            config=cfg,
            secrets=dict(secrets or {}),
            now=(now_fn or time.time),
        )

    def read_service_config(self, service_id: str) -> dict[str, Any]:
        """Read the ``service:`` block from the contract YAML.
        Cached. Public so dispatch helpers (URL building, auth
        headers) can read host/port/api_key_env without re-parsing."""
        with self._lock:
            cached = self._config_cache.get(service_id)
            if cached is not None:
                return cached
        path = self._contracts_dir / f"{service_id}.yaml"
        cfg: dict[str, Any] = {}
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    svc = data.get("service") or {}
                    if isinstance(svc, dict):
                        cfg = dict(svc)
            except yaml.YAMLError as exc:
                logger.warning("contract %s malformed: %s", path, exc)
        with self._lock:
            self._config_cache[service_id] = cfg
        return cfg

    def _read_lifecycle_class(self, service_id: str) -> str:
        path = self._contracts_dir / f"{service_id}.yaml"
        if not path.is_file():
            return ""
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return ""
        if not isinstance(data, dict):
            return ""
        return str(((data.get("plugin") or {}).get("lifecycle_class") or "")).strip()


# ============================================================================
# Probe dispatch
# ============================================================================


def dispatch_probe(
    spec: ProbeSpec,
    *,
    resolver: LifecycleResolver,
    now: float,
    secrets: Mapping[str, str] | None = None,
) -> ProbeResult:
    """Single entry point for executing a probe. Maps ``spec`` to the
    right handler and returns a uniform ``ProbeResult``. Each handler
    catches its own exceptions — a probe MUST always return a result,
    never raise."""
    if isinstance(spec, LifecycleProbe):
        return _probe_lifecycle(spec, resolver, now, secrets)
    if isinstance(spec, HttpJsonProbe):
        return _probe_http_json(spec, resolver, now, secrets)
    if isinstance(spec, HttpTextProbe):
        return _probe_http_text(spec, resolver, now, secrets)
    if isinstance(spec, HttpStatusProbe):
        return _probe_http_status(spec, resolver, now, secrets)
    if isinstance(spec, FileJsonProbe):
        return _probe_file_json(spec, now)
    if isinstance(spec, FileTextProbe):
        return _probe_file_text(spec, now)
    if isinstance(spec, K8sResourceProbe):
        return ProbeResult.unknown(
            "k8s_resource probe not implemented in orchestrator (Phase 5+)",
            evaluated_at=now,
        )
    if isinstance(spec, K8sExecProbe):
        return ProbeResult.unknown(
            "k8s_exec probe not implemented in orchestrator (Phase 5+)",
            evaluated_at=now,
        )
    return ProbeResult.unknown(
        f"unknown probe kind {type(spec).__name__}",
        evaluated_at=now,
    )


# --- per-kind probe implementations -----------------------------------


def _probe_lifecycle(
    spec: LifecycleProbe,
    resolver: LifecycleResolver,
    now: float,
    secrets: Mapping[str, str] | None,
) -> ProbeResult:
    impl = resolver.resolve(spec.service)
    if impl is None:
        return ProbeResult.failed(
            f"no lifecycle for service {spec.service!r}",
            evaluated_at=now,
        )
    method = getattr(impl, spec.method, None)
    if not callable(method):
        return ProbeResult.failed(
            f"lifecycle for {spec.service!r} has no method {spec.method!r}",
            evaluated_at=now,
        )
    ctx = resolver.context_for(spec.service, secrets=secrets, now_fn=lambda: now)
    try:
        result = method(ctx)
    except Exception as exc:  # noqa: BLE001 - probes never raise
        return ProbeResult.unknown(
            f"lifecycle {spec.service}.{spec.method} raised: {exc}",
            evidence={"error": str(exc)},
            evaluated_at=now,
        )
    if not isinstance(result, ProbeResult):
        return ProbeResult.unknown(
            f"lifecycle {spec.service}.{spec.method} returned non-ProbeResult: "
            f"{type(result).__name__}",
            evaluated_at=now,
        )
    return result


def _probe_http_json(
    spec: HttpJsonProbe,
    resolver: LifecycleResolver,
    now: float,
    secrets: Mapping[str, str] | None,
) -> ProbeResult:
    url = _build_service_url(spec.service, spec.path, resolver)
    if not url:
        return ProbeResult.failed(
            f"can't build url for service {spec.service!r}",
            evaluated_at=now,
        )
    headers = _auth_headers(spec.auth, spec.service, secrets, resolver)
    try:
        body, status = _http_get(url, headers)
    except urllib.error.HTTPError as exc:
        return ProbeResult.failed(
            f"HTTP {exc.code} from {url}",
            evidence={"url": url, "http_status": exc.code},
            evaluated_at=now,
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return ProbeResult.unknown(
            f"unreachable at {url}: {exc}",
            evidence={"url": url, "error": str(exc)},
            evaluated_at=now,
        )
    if status != 200:
        return ProbeResult.failed(
            f"HTTP {status} from {url}",
            evidence={"url": url, "http_status": status},
            evaluated_at=now,
        )
    import json as _json
    try:
        response = _json.loads(body)
    except _json.JSONDecodeError as exc:
        return ProbeResult.failed(
            f"non-JSON body from {url}: {exc}",
            evidence={"url": url, "http_status": status},
            evaluated_at=now,
        )
    return _classify_assert(
        spec.assert_expr, {"response": response},
        url, status, now,
    )


def _probe_http_text(
    spec: HttpTextProbe,
    resolver: LifecycleResolver,
    now: float,
    secrets: Mapping[str, str] | None,
) -> ProbeResult:
    url = _build_service_url(spec.service, spec.path, resolver)
    if not url:
        return ProbeResult.failed(
            f"can't build url for service {spec.service!r}",
            evaluated_at=now,
        )
    headers = _auth_headers(spec.auth, spec.service, secrets, resolver)
    try:
        body, status = _http_get(url, headers)
    except urllib.error.HTTPError as exc:
        return ProbeResult.failed(
            f"HTTP {exc.code} from {url}",
            evidence={"url": url, "http_status": exc.code},
            evaluated_at=now,
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return ProbeResult.unknown(
            f"unreachable at {url}: {exc}",
            evidence={"url": url, "error": str(exc)},
            evaluated_at=now,
        )
    if status != 200:
        return ProbeResult.failed(
            f"HTTP {status} from {url}",
            evidence={"url": url, "http_status": status},
            evaluated_at=now,
        )
    return _classify_assert(
        spec.assert_expr, {"response": body, "data": body},
        url, status, now,
    )


def _probe_http_status(
    spec: HttpStatusProbe,
    resolver: LifecycleResolver,
    now: float,
    secrets: Mapping[str, str] | None,
) -> ProbeResult:
    url = _build_service_url(spec.service, spec.path, resolver)
    if not url:
        return ProbeResult.failed(
            f"can't build url for service {spec.service!r}",
            evaluated_at=now,
        )
    headers = _auth_headers(spec.auth, spec.service, secrets, resolver)
    status: int = 0
    try:
        _, status = _http_get(url, headers)
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return ProbeResult.unknown(
            f"unreachable at {url}: {exc}",
            evidence={"url": url, "error": str(exc)},
            evaluated_at=now,
        )
    return _classify_assert(
        spec.assert_expr, {"status": status, "response": status},
        url, status, now,
    )


def _probe_file_json(spec: FileJsonProbe, now: float) -> ProbeResult:
    path = _resolve_file_path(spec.path)
    if not path.is_file():
        if spec.skip_if_missing:
            return ProbeResult.ok(
                f"skip_if_missing: {path} absent",
                evidence={"path": str(path), "skipped": True},
                evaluated_at=now,
            )
        return ProbeResult.failed(
            f"file not found: {path}",
            evidence={"path": str(path)},
            evaluated_at=now,
        )
    import json as _json
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError) as exc:
        return ProbeResult.failed(
            f"file unreadable as JSON: {path}: {exc}",
            evidence={"path": str(path)},
            evaluated_at=now,
        )
    return _classify_assert(
        spec.assert_expr, {"data": data}, str(path), 0, now,
    )


def _probe_file_text(spec: FileTextProbe, now: float) -> ProbeResult:
    path = _resolve_file_path(spec.path)
    if not path.is_file():
        if spec.skip_if_missing:
            return ProbeResult.ok(
                f"skip_if_missing: {path} absent",
                evidence={"path": str(path), "skipped": True},
                evaluated_at=now,
            )
        return ProbeResult.failed(
            f"file not found: {path}",
            evidence={"path": str(path)},
            evaluated_at=now,
        )
    try:
        data = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ProbeResult.failed(
            f"file unreadable: {path}: {exc}",
            evidence={"path": str(path)},
            evaluated_at=now,
        )
    return _classify_assert(
        spec.assert_expr, {"data": data}, str(path), 0, now,
    )


# ============================================================================
# Ensurer dispatch
# ============================================================================


def dispatch_ensurer(
    spec: EnsurerSpec,
    *,
    resolver: LifecycleResolver,
    now: float,
    secrets: Mapping[str, str] | None = None,
) -> Outcome[Any]:
    """Single entry point for ensurer execution. Returns an
    ``Outcome`` whose ``transient`` flag drives the orchestrator's
    cooldown decision.

    ``deploy`` and ``infra`` ensurers are intentionally no-ops in
    Phase 4 — the orchestrator can't run them. Returns
    ``Outcome.success`` with ``reason=externally_ensured`` evidence.
    The re-probe afterwards still happens; if the service is up, the
    promise records ok; if not, the operator looks at the deploy
    tooling.
    """
    if isinstance(spec, LifecycleEnsurer):
        return _ensure_lifecycle(spec, resolver, now, secrets)
    if isinstance(spec, JobEnsurer):
        return _ensure_job(spec, now)
    if isinstance(spec, DeployEnsurer):
        return Outcome.success(
            None,
            evidence={"reason": "externally_ensured", "target": spec.target},
        )
    if isinstance(spec, InfraEnsurer):
        return Outcome.success(
            None,
            evidence={"reason": "externally_ensured", "operator": spec.operator},
        )
    return Outcome.failure(
        f"unknown ensurer kind {type(spec).__name__}",
        transient=False,
    )


def _ensure_lifecycle(
    spec: LifecycleEnsurer,
    resolver: LifecycleResolver,
    now: float,
    secrets: Mapping[str, str] | None,
) -> Outcome[Any]:
    impl = resolver.resolve(spec.service)
    if impl is None:
        return Outcome.failure(
            f"no lifecycle for service {spec.service!r}",
            transient=False,
        )
    method = getattr(impl, spec.method, None)
    if not callable(method):
        return Outcome.failure(
            f"lifecycle for {spec.service!r} has no method {spec.method!r}",
            transient=False,
        )
    ctx = resolver.context_for(spec.service, secrets=secrets, now_fn=lambda: now)
    try:
        result = method(ctx)
    except Exception as exc:  # noqa: BLE001 - ensurers never raise
        return Outcome.failure(
            f"lifecycle {spec.service}.{spec.method} raised: {exc}",
            transient=True,
            evidence={"error": str(exc)},
        )
    if not isinstance(result, Outcome):
        return Outcome.failure(
            f"lifecycle {spec.service}.{spec.method} returned non-Outcome: "
            f"{type(result).__name__}",
            transient=False,
        )
    return result


def _ensure_job(spec: JobEnsurer, now: float) -> Outcome[Any]:
    if not spec.job_name:
        return Outcome.failure(
            "JobEnsurer with no job_name",
            transient=False,
        )
    try:
        from media_stack.application.jobs.framework import run_job
    except ImportError as exc:
        return Outcome.failure(
            f"job framework unavailable: {exc}",
            transient=False,
        )
    try:
        result = run_job(spec.job_name, source="orchestrator_shadow")
    except Exception as exc:  # noqa: BLE001
        return Outcome.failure(
            f"run_job({spec.job_name!r}) raised: {exc}",
            transient=True,
            evidence={"error": str(exc)},
        )
    if not isinstance(result, dict):
        return Outcome.failure(
            f"run_job({spec.job_name!r}) returned non-dict",
            transient=True,
        )
    if result.get("error"):
        return Outcome.failure(
            f"run_job({spec.job_name!r}) error: {result['error']}",
            transient=True,
            evidence=dict(result),
        )
    if result.get("status") == "ok" or result.get("skipped"):
        return Outcome.success(None, evidence=dict(result))
    return Outcome.failure(
        f"run_job({spec.job_name!r}) inconclusive: {result}",
        transient=True,
        evidence=dict(result),
    )


# ============================================================================
# Helpers
# ============================================================================


def _default_contracts_dir() -> Path:
    """Use the shared root resolver so dev + container layouts both
    work. Returns the ``services/`` subdirectory."""
    from media_stack.infrastructure.promises.registry import (
        default_contracts_root,
    )
    return default_contracts_root() / "services"


def _instantiate(dotted: str, service_id: str) -> Any:
    if ":" not in dotted:
        raise ValueError(f"lifecycle_class must be 'mod.path:Class', got {dotted!r}")
    mod_path, cls_name = dotted.split(":", 1)
    mod = importlib.import_module(mod_path)
    cls = getattr(mod, cls_name)
    for kwargs in ({"service_id": service_id}, {}):
        try:
            return cls(**kwargs)
        except TypeError:
            continue
    raise ValueError(
        f"could not instantiate {dotted} with () or (service_id=...)",
    )


def _build_service_url(
    service_id: str, path: str, resolver: LifecycleResolver,
) -> str:
    """Resolve ``service_id + path`` to a URL.

    First tries the contracts/services/<id>.yaml file. If that's
    absent, falls back to a small set of synthetic service ids the
    legacy promise CLI also recognizes:

      * ``controller``      — the controller's own API
      * ``gateway_https``   — the public HTTPS edge (envoy/Traefik)
      * ``gateway_http``    — the public HTTP edge (redirects to HTTPS)

    Without these synthetic resolutions the orchestrator can't probe
    promises like ``adaptive-search-scheduled`` (controller jobs API)
    or ``gateway-https-listener-up`` (gateway health) — they'd all
    return ``can't build url for service`` even though the legacy
    CLI handles them fine.
    """
    cfg = resolver.read_service_config(service_id)
    host = (cfg.get("host") or "").strip()
    port = cfg.get("port")
    if host and port:
        scheme = (cfg.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}{path}"
    return _synthetic_service_url(service_id, path)


def _synthetic_service_url(service_id: str, path: str) -> str:
    """Hardcoded URL builders for service ids without a
    contracts/services YAML.

    The orchestrator runs INSIDE the controller container, which
    means it has to reach gateways at their cluster-internal
    addresses, NOT the host's published 443/80 ports the legacy
    ``probe_promises.py`` CLI uses (it runs on the host shell). On
    compose, envoy's TLS listener is on container port 8880 and
    plain on 8080 (host:443 → 8880 / host:80 → 8080 mapping); on
    k8s, the envoy Service exposes port 80 and TLS terminates at
    the ingress before routing.

    Returns ``""`` when the service id isn't recognized.
    """
    import os as _os
    in_k8s = bool(_os.environ.get("KUBERNETES_SERVICE_HOST"))
    if service_id == "controller":
        # Controller's own HTTP API — same process; localhost works
        # on both platforms.
        return f"http://localhost:9100{path}"
    if service_id == "gateway_http":
        if in_k8s:
            return f"http://envoy:80{path}"
        # Compose: envoy:8080 is the plain-HTTP listener (mapped
        # from host:80).
        return f"http://envoy:8080{path}"
    if service_id == "gateway_https":
        if in_k8s:
            # Ingress terminates TLS; envoy serves plain HTTP and
            # routes by Host header.
            return f"http://envoy:80{path}"
        # Compose: envoy:8880 is the TLS listener (mapped from
        # host:443). Self-signed cert — caller's HTTP get disables
        # verification for synthetic gateway probes.
        return f"https://envoy:8880{path}"
    return ""


def _is_synthetic_gateway_url(url: str) -> bool:
    """True if the URL hits the compose internal gateway (envoy:8880).
    Used by the HTTP get to relax TLS verification — the gateway
    serves a self-signed cert valid for the public hostname, not
    ``envoy``. Verifying would always fail; skipping is sound
    because the probe's question is "is it answering?", not "does
    the cert chain validate?"."""
    return "://envoy:8880" in url


def _auth_headers(
    auth: str,
    service_id: str,
    secrets: Mapping[str, str] | None,
    resolver: LifecycleResolver,
) -> dict[str, str]:
    """Build the auth-header dict for an HTTP probe.

    Supported ``auth`` values:

      * ``none``         — no headers (default)
      * ``api_key``      — read ``api_key_env`` from contract YAML +
                           env/secrets, set the contract's
                           ``auth_mode`` header (default
                           ``X-Api-Key``)
      * ``jellyfin_key`` — same as ``api_key`` for jellyfin
                           specifically. Promises authored with this
                           explicit auth type predate the lifecycle
                           Protocol; alias preserved for back-compat
                           so the meta-ratchet's existing
                           ``ensured_by`` strings continue to point
                           at probes that resolve.
      * ``controller_basic`` / ``qbit_basic`` — not yet implemented
                           in the orchestrator. Phase 4d / 5+ will
                           add these once enough cross-service
                           promises depend on them.
    """
    auth_l = (auth or "none").lower()
    if auth_l == "none":
        return {}
    if auth_l == "jellyfin_key":
        # Jellyfin's auth header is X-Emby-Token; resolve via the
        # service's contract (which sets auth_mode=X-Emby-Token).
        # The api_key_env there is JELLYFIN_API_KEY.
        return _api_key_headers("jellyfin", secrets, resolver)
    if auth_l == "api_key":
        return _api_key_headers(service_id, secrets, resolver)
    if auth_l == "controller_basic":
        return _controller_basic_headers(secrets)
    # qbit_basic / others — not yet implemented; let the probe go
    # through unauthenticated so the resulting 401 surfaces in
    # run-history. Phase 5+ will wire these once promises that need
    # them are confirmed to be in scope.
    return {}


def _controller_basic_headers(
    secrets: Mapping[str, str] | None,
) -> dict[str, str]:
    """HTTP Basic against the controller's own API as the seeded
    stack admin. Same flow ``probe_promises.py`` uses; lets the
    orchestrator probe controller-served promises like
    ``adaptive-search-scheduled`` and ``foundational-jobs-run-before-
    app-jobs`` instead of always landing on 401."""
    import base64 as _b64
    import os as _os
    user = ""
    pwd = ""
    if secrets is not None:
        user = (secrets.get("STACK_ADMIN_USERNAME") or "").strip()
        pwd = (secrets.get("STACK_ADMIN_PASSWORD") or "").strip()
    if not user:
        user = (_os.environ.get("STACK_ADMIN_USERNAME") or "admin").strip()
    if not pwd:
        pwd = (_os.environ.get("STACK_ADMIN_PASSWORD") or "").strip()
    if not pwd:
        return {}
    token = _b64.b64encode(f"{user}:{pwd}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _api_key_headers(
    service_id: str,
    secrets: Mapping[str, str] | None,
    resolver: LifecycleResolver,
) -> dict[str, str]:
    cfg = resolver.read_service_config(service_id)
    env_var = (cfg.get("api_key_env") or "").strip()
    if not env_var:
        return {}
    key = ""
    if secrets is not None:
        key = (secrets.get(env_var) or "").strip()
    if not key:
        import os as _os
        key = (_os.environ.get(env_var) or "").strip()
    if not key:
        return {}
    auth_mode = (cfg.get("auth_mode") or "X-Api-Key").strip()
    return {auth_mode: key}


def _http_get(url: str, headers: Mapping[str, str]) -> tuple[str, int]:
    req = urllib.request.Request(url, headers=dict(headers))
    kwargs: dict[str, Any] = {"timeout": _PROBE_TIMEOUT_SECONDS}
    if _is_synthetic_gateway_url(url):
        # Internal gateway probe — envoy serves a self-signed cert
        # valid for the public hostname, not "envoy". Skip
        # verification; the probe's question is reachability, not
        # cert chain.
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        kwargs["context"] = ctx
    with urllib.request.urlopen(req, **kwargs) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return body, resp.status


def _resolve_file_path(rel: str) -> Path:
    """Resolve a file probe's ``path`` against the controller's
    config root. Falls back to ``/srv-config`` when ``CONFIG_ROOT``
    env is unset — matching ``resolve_run_history_path``'s fallback
    so file probes work in containers that don't explicitly set the
    env (the typical case)."""
    import os as _os
    config_root = (_os.environ.get("CONFIG_ROOT") or "/srv-config").strip()
    return Path(config_root) / rel


def _classify_assert(
    expr: str,
    scope: Mapping[str, Any],
    url: str,
    status: int,
    now: float,
) -> ProbeResult:
    """Run the YAML ``assert:`` expression via the centralized
    evaluator. Truthy → ok; falsy → failed; helper-reported error →
    unknown."""
    if not expr:
        return ProbeResult.failed(
            "probe missing assert expression",
            evaluated_at=now,
        )
    ok, detail = _eval_assert(expr, scope)
    if ok:
        return ProbeResult.ok(
            "probe asserted ok",
            evidence={"url": url, "http_status": status},
            evaluated_at=now,
        )
    if detail.startswith("assert eval error"):
        return ProbeResult.unknown(
            detail,
            evidence={"url": url, "http_status": status, "expr": expr},
            evaluated_at=now,
        )
    return ProbeResult.failed(
        detail or "probe assertion was falsy",
        evidence={"url": url, "http_status": status, "expr": expr},
        evaluated_at=now,
    )


__all__ = ["LifecycleResolver", "dispatch_ensurer", "dispatch_probe"]
