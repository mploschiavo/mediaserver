"""Probe + ensurer dispatch tables for the promise orchestrator.

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
from http import HTTPStatus
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


# Reused YAML-assert evaluator — single auditable eval site at
# ``infrastructure.promises.assert_eval`` (extracted out of the
# probe_promises CLI in Phase 5e.1 so the CLI can be retired
# independently of the orchestrator).
from media_stack.infrastructure.promises.assert_eval import (
    evaluate as _eval_assert,
)


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
        return _probe_k8s_resource(spec, now)
    if isinstance(spec, K8sExecProbe):
        return _probe_k8s_pod_command(spec, now)
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
        body, status, _resp_headers = _PROBE_HTTP_CLIENT.get_following_redirects(url, headers)
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
        body, status, _resp_headers = _PROBE_HTTP_CLIENT.get_following_redirects(url, headers)
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
    resp_headers: dict[str, str] = {}
    try:
        _, status, resp_headers = _PROBE_HTTP_CLIENT.get_no_redirects(
            url, headers,
        )
    except urllib.error.HTTPError as exc:
        # 30x already returns as a normal tuple via _http_get's
        # no-redirect path; this branch handles 4xx / 5xx.
        status = exc.code
        try:
            resp_headers = {k.lower(): v for k, v in exc.headers.items()}
        except Exception:
            resp_headers = {}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return ProbeResult.unknown(
            f"unreachable at {url}: {exc}",
            evidence={"url": url, "error": str(exc)},
            evaluated_at=now,
        )
    return _classify_assert(
        spec.assert_expr,
        {"status": status, "response": status, "headers": resp_headers},
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


_K8S_RESOURCE_KINDS = {
    # Namespaced kinds — listed via list_namespaced_<kind> when a
    # namespace is given; cluster-wide via list_<kind>_for_all_namespaces.
    "pod": ("CoreV1Api", "list_namespaced_pod", "list_pod_for_all_namespaces"),
    "service": ("CoreV1Api", "list_namespaced_service",
                "list_service_for_all_namespaces"),
    "pvc": ("CoreV1Api", "list_namespaced_persistent_volume_claim",
            "list_persistent_volume_claim_for_all_namespaces"),
    "secret": ("CoreV1Api", "list_namespaced_secret",
               "list_secret_for_all_namespaces"),
    "configmap": ("CoreV1Api", "list_namespaced_config_map",
                  "list_config_map_for_all_namespaces"),
    "deployment": ("AppsV1Api", "list_namespaced_deployment",
                   "list_deployment_for_all_namespaces"),
    "ingress": ("NetworkingV1Api", "list_namespaced_ingress",
                "list_ingress_for_all_namespaces"),
    # Cluster-scoped kind — only the cluster-wide call applies.
    "pv": ("CoreV1Api", None, "list_persistent_volume"),
}


_K8S_ROUTING_VAR_NAMES = (
    "gateway_host", "stack_subdomain", "base_domain", "app_path_prefix",
)


def _resolve_routing_vars_for_substitution() -> dict[str, str]:
    """Read the live merged routing config so probe commands can
    substitute ``${gateway_host}`` etc. Cached per-process — routing
    config changes are operator-driven and rare. Failure-tolerant: if
    the config service is unavailable for any reason, returns an
    empty dict and the substitution becomes a no-op."""
    cached = getattr(_resolve_routing_vars_for_substitution, "_cached", None)
    if cached is not None:
        return cached
    out: dict[str, str] = {}
    try:
        from media_stack.api.services.config import get_routing
        routing = get_routing() or {}
    except Exception as exc:  # noqa: BLE001 - probes never raise
        logger.debug("routing-var resolution skipped: %s", exc)
        _resolve_routing_vars_for_substitution._cached = out
        return out
    for key in _K8S_ROUTING_VAR_NAMES:
        val = str(routing.get(key) or "").strip()
        if val:
            out[key] = val
    _resolve_routing_vars_for_substitution._cached = out
    return out


def _substitute_routing_vars(text: str, routing_vars: Mapping[str, str]) -> str:
    """Replace ``${var}`` placeholders. Bypassed when the var is
    missing from ``routing_vars`` — the caller's ``skip_if_unset``
    machinery has already decided what to do with that case."""
    out = text
    for key, val in routing_vars.items():
        out = out.replace("${" + key + "}", val)
    return out


def _load_k8s_clients() -> tuple[Any, Any, Any] | None:
    """Return ``(CoreV1Api, AppsV1Api, NetworkingV1Api)`` instances or
    ``None`` if k8s isn't available. Logs at DEBUG on failure — callers
    surface ``unknown`` to the orchestrator so cooldown applies."""
    try:
        from kubernetes import client as _k8s
        from kubernetes import config as _kconfig
    except ImportError as exc:
        logger.debug("kubernetes client unavailable: %s", exc)
        return None
    try:
        _kconfig.load_incluster_config()
    except Exception as exc:  # noqa: BLE001 - covers ConfigException too
        try:
            _kconfig.load_kube_config()
        except Exception as exc2:  # noqa: BLE001
            logger.debug(
                "k8s config load failed (incluster: %s; kubeconfig: %s)",
                exc, exc2,
            )
            return None
    try:
        return _k8s.CoreV1Api(), _k8s.AppsV1Api(), _k8s.NetworkingV1Api()
    except Exception as exc:  # noqa: BLE001
        logger.debug("k8s client construction failed: %s", exc)
        return None


def _serialize_k8s_item(item: Any) -> dict[str, Any]:
    """Convert a kubernetes client model to the API JSON shape
    (camelCase keys: ``imagePullSecrets``, ``persistentVolumeReclaimPolicy``,
    ``claimRef``...) that ``kubectl -o json`` produces and that every
    k8s_resource promise's assert expression was authored against.

    Note: ``item.to_dict()`` returns Python snake_case (e.g.
    ``image_pull_secrets``). Using that shape silently fails every
    assert that references a camelCase key, even when the world
    actually satisfies the invariant.
    """
    try:
        from kubernetes import client as _k8s
        return _k8s.ApiClient().sanitize_for_serialization(item)
    except Exception:  # noqa: BLE001
        # Fallback: hand-roll the dict but keep the snake_case keys
        # so at least probes don't crash if sanitize fails for some
        # exotic resource type.
        return item.to_dict() if hasattr(item, "to_dict") else dict(item)


def _probe_k8s_resource(spec: K8sResourceProbe, now: float) -> ProbeResult:
    """List a Kubernetes resource via the in-cluster API and evaluate
    the assertion against ``resources`` (a list of dicts).

    Mirrors the legacy CLI's contract: same ``resources`` scope name,
    same assertion language, same kind vocabulary. Source-of-truth
    differences from the legacy:

      * Uses the kubernetes Python client + the controller's service
        account (no kubectl shell-out, no kubeconfig).
      * Cluster-scoped kinds (``pv``) ignore ``namespace`` instead
        of failing with "namespace must be empty".
    """
    kind = (spec.resource_kind or "").lower().strip()
    if not kind:
        return ProbeResult.failed(
            "k8s_resource probe missing 'kind'", evaluated_at=now,
        )
    mapping = _K8S_RESOURCE_KINDS.get(kind)
    if mapping is None:
        return ProbeResult.failed(
            f"k8s_resource: unsupported kind {kind!r}",
            evaluated_at=now,
        )
    api_attr, ns_method, allns_method = mapping

    apis = _load_k8s_clients()
    if apis is None:
        return ProbeResult.unknown(
            "k8s client unavailable (running outside cluster?)",
            evaluated_at=now,
        )
    core_api, apps_api, net_api = apis
    api = {"CoreV1Api": core_api, "AppsV1Api": apps_api,
           "NetworkingV1Api": net_api}[api_attr]

    label_selector = (spec.label_selector or "").strip()
    namespace = (spec.namespace or "").strip()
    kwargs: dict[str, Any] = {}
    if label_selector:
        kwargs["label_selector"] = label_selector
    try:
        if namespace and ns_method:
            method = getattr(api, ns_method)
            response = method(namespace=namespace, **kwargs)
        else:
            method = getattr(api, allns_method)
            response = method(**kwargs)
    except Exception as exc:  # noqa: BLE001 - probes never raise
        return ProbeResult.unknown(
            f"k8s_resource list failed: {exc.__class__.__name__}",
            evidence={"kind": kind, "namespace": namespace,
                      "label_selector": label_selector,
                      "error": str(exc)[:200]},
            evaluated_at=now,
        )

    items = getattr(response, "items", None) or []
    resources = [_serialize_k8s_item(item) for item in items]
    label = f"k8s://{kind}" + (f"/{namespace}" if namespace else "")
    return _classify_assert(
        spec.assert_expr, {"resources": resources},
        label, 0, now,
    )


def _probe_k8s_pod_command(spec: K8sExecProbe, now: float) -> ProbeResult:
    """Run a command inside a Running pod and evaluate the assertion
    against its stdout (exposed as ``data``).

    Same contract as the legacy CLI: ``${var}`` substitution from
    routing config in both the command and the assert expression;
    ``skip_if_unset`` skips with a pass when the named routing var
    isn't configured (treated as "this promise is moot for this
    deployment", not failure).
    """
    namespace = (spec.namespace or "").strip()
    pod_label = (spec.pod_label or "").strip()
    container = (spec.container or "").strip()
    command = list(spec.command or ())
    skip_if_unset = (spec.skip_if_unset or "").strip()

    if not namespace or not pod_label or not command:
        return ProbeResult.failed(
            "k8s_exec missing namespace/pod_label/command",
            evaluated_at=now,
        )

    routing_vars = _resolve_routing_vars_for_substitution()
    if skip_if_unset and not routing_vars.get(skip_if_unset):
        # Promise is N/A for this deployment — treat as ok per the
        # legacy contract (operator hasn't configured the relevant
        # routing var so the assert can't possibly hold or matter).
        return ProbeResult.ok(
            f"skipped ({skip_if_unset} not configured)",
            evidence={"skipped": True, "reason": skip_if_unset},
            evaluated_at=now,
        )

    resolved_cmd = [_substitute_routing_vars(str(p), routing_vars)
                    for p in command]
    resolved_assert = _substitute_routing_vars(
        str(spec.assert_expr or ""), routing_vars,
    )

    apis = _load_k8s_clients()
    if apis is None:
        return ProbeResult.unknown(
            "k8s client unavailable (running outside cluster?)",
            evaluated_at=now,
        )
    core_api, _, _ = apis

    try:
        pods = core_api.list_namespaced_pod(
            namespace=namespace, label_selector=pod_label,
            field_selector="status.phase=Running",
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult.unknown(
            f"pod lookup failed: {exc.__class__.__name__}",
            evidence={"pod_label": pod_label, "error": str(exc)[:200]},
            evaluated_at=now,
        )
    pod_items = getattr(pods, "items", None) or []
    if not pod_items:
        return ProbeResult.failed(
            f"no Running pod matches {pod_label!r}",
            evidence={"pod_label": pod_label, "namespace": namespace},
            evaluated_at=now,
        )
    pod_name = pod_items[0].metadata.name

    try:
        from kubernetes.stream import stream as _k8s_stream
        kwargs: dict[str, Any] = {
            "command": resolved_cmd, "stdout": True, "stderr": True,
            "stdin": False, "tty": False,
        }
        if container:
            kwargs["container"] = container
        stdout = _k8s_stream(
            core_api.connect_get_namespaced_pod_exec,
            pod_name, namespace, **kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult.unknown(
            f"pod exec failed: {exc.__class__.__name__}",
            evidence={"pod": pod_name, "error": str(exc)[:200]},
            evaluated_at=now,
        )
    label = f"k8s://{namespace}/{pod_name}"
    return _classify_assert(
        resolved_assert, {"data": stdout or ""},
        label, 0, now,
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


# HTTP redirect status codes — stored as IntEnum members from the
# stdlib so the file carries no magic ints. ``IntEnum`` subclasses
# ``int`` so ``exc.code in _HTTP_REDIRECT_STATUSES`` works directly.
_HTTP_REDIRECT_STATUSES: frozenset[HTTPStatus] = frozenset({
    HTTPStatus.MOVED_PERMANENTLY,
    HTTPStatus.FOUND,
    HTTPStatus.SEE_OTHER,
    HTTPStatus.TEMPORARY_REDIRECT,
    HTTPStatus.PERMANENT_REDIRECT,
})

# urllib's response bodies arrive as bytes; we decode with
# error-replace so non-utf8 noise (rare for control-plane APIs)
# never raises in the probe path.
_HTTP_RESPONSE_ENCODING = "utf-8"
_HTTP_DECODE_ERRORS_POLICY = "replace"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disable urllib's default 30x auto-follow.

    The default ``HTTPRedirectHandler`` returns a new ``Request`` and
    urllib re-issues it transparently. ``http_status`` probes that
    inspect the redirect itself (e.g. ``gateway-http-redirects-to-https``
    asserts ``status in (301, 302)``) need to see the original response.
    Returning ``None`` from these handlers makes urllib raise
    ``HTTPError`` for the 30x — callers translate that back into a
    plain status + response object.
    """

    def http_error_301(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any,
    ) -> None:
        return None

    def http_error_302(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any,
    ) -> None:
        return None

    def http_error_303(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any,
    ) -> None:
        return None

    def http_error_307(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any,
    ) -> None:
        return None

    def http_error_308(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any,
    ) -> None:
        return None


class _ProbeHttpClient:
    """HTTP client for probe dispatchers.

    Two named methods replace what would otherwise be a
    ``follow_redirects: bool`` flag — see boolean-flag-arg ratchet.
    Constructor-injected timeout; the gateway-self-signed-cert SSL
    context is built per-request because it depends on the URL.
    """

    def __init__(self, timeout: float = _PROBE_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    def get_following_redirects(
        self, url: str, headers: Mapping[str, str],
    ) -> tuple[str, int, dict[str, str]]:
        """GET ``url``; transparently follow 30x to the final resource.

        Used by ``http_text`` / ``http_json`` probes that assert
        against the body of the redirected-to resource. urllib's
        default opener is sufficient — ``urlopen`` follows 30x.
        """
        req, kwargs = self._build_request(url, headers)
        with urllib.request.urlopen(req, **kwargs) as resp:
            return self._extract(resp)

    def get_no_redirects(
        self, url: str, headers: Mapping[str, str],
    ) -> tuple[str, int, dict[str, str]]:
        """GET ``url``; surface 30x responses to the caller as-is.

        Used by ``http_status`` probes whose assert inspects the
        redirect itself. The no-op handler turns 30x into HTTPError;
        we translate that back into the canonical 3-tuple.

        ``OpenerDirector.open()`` does NOT accept ``context=`` (only
        ``urlopen()`` does). When the request needs a custom SSL
        context, we install an ``HTTPSHandler(context=ctx)`` on the
        opener instead so the per-call signature stays plain.
        """
        req, kwargs = self._build_request(url, headers)
        ssl_context = kwargs.pop("context", None)
        handlers: list[Any] = [_NoRedirectHandler]
        if ssl_context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(req, **kwargs) as resp:
                return self._extract(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in _HTTP_REDIRECT_STATUSES:
                return self._extract_from_error(exc)
            raise

    def _build_request(
        self, url: str, headers: Mapping[str, str],
    ) -> tuple[urllib.request.Request, dict[str, Any]]:
        req = urllib.request.Request(url, headers=dict(headers))
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if _is_synthetic_gateway_url(url):
            # Envoy serves a self-signed cert valid for the public
            # hostname, not "envoy". The probe asks reachability,
            # not cert chain.
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            kwargs["context"] = ctx
        return req, kwargs

    def _extract(self, resp: Any) -> tuple[str, int, dict[str, str]]:
        body = resp.read().decode(
            _HTTP_RESPONSE_ENCODING, errors=_HTTP_DECODE_ERRORS_POLICY,
        )
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        return body, resp.status, resp_headers

    def _extract_from_error(
        self, exc: urllib.error.HTTPError,
    ) -> tuple[str, int, dict[str, str]]:
        # ``exc.read()`` can raise on already-closed responses; let
        # those propagate (don't swallow per the no-silent-failure
        # ratchet). 30x bodies are usually empty anyway.
        body = exc.read().decode(
            _HTTP_RESPONSE_ENCODING, errors=_HTTP_DECODE_ERRORS_POLICY,
        )
        resp_headers = {k.lower(): v for k, v in exc.headers.items()}
        return body, exc.code, resp_headers


# Module-level singleton — probe dispatchers go through this class.
# Tests patch ``urllib.request.urlopen`` (follow-redirects path) or
# ``urllib.request.build_opener`` (no-redirects path) directly.
_PROBE_HTTP_CLIENT = _ProbeHttpClient()


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
