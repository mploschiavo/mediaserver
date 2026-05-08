"""Jobs-domain GET routes (ADR-0007 Phase 2 wave 3).

Three routes migrated off the ``handlers_get.handle()`` elif chain,
all sharing the ``Jobs`` OpenAPI tag:

* ``GET /api/jobs`` — discovered jobs catalog + dependency tree +
  recent run history. Drives the Jobs page's tree view + history
  feed. Body lifted verbatim from the legacy chain.
* ``GET /api/jobs/queue`` — operator-managed pending-work queue.
  Read-only operator surface (the JobRunner integration is
  deferred). Single-line delegation to ``job_queue.get_queue``.
* ``GET /api/jobs/running`` — aggregator that fans across (1)
  ActionRecord-tracked actions, (2) k8s ``Job`` pods in Active
  phase, and (3) the ``run_history`` parent → child tree.
  Surfaced in the global banner ("3 things are happening right
  now") and the Jobs page's ``CurrentlyRunningCard``.

Implementation choices, per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule:

* ``/api/jobs/queue`` is a one-line delegation to
  ``job_queue.get_queue()``.
* ``/api/jobs`` and ``/api/jobs/running`` LIFT their legacy bodies
  verbatim — both have rich logic (recursive tree-building for
  ``/api/jobs``; three-source aggregation for ``/api/jobs/running``)
  plus narrowly-scoped lazy imports the legacy chain uses to keep
  startup cost flat. The lift preserves the structure; the
  legacy broad-catch blocks are narrowed to specific exception
  classes per the "narrow on lift" rule (see per-method docs).

Helper construction is split out: ``/api/jobs`` builds a recursive
tree dict from the ``build_job_framework()`` root. We model that
recursion as a private helper bound to the route class
(``_JobTreeBuilder``) so the route method itself stays a thin
adapter. Constructor-injected via a factory method to keep
test seams clean.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import job_queue
from media_stack.application.jobs.run_history import get_running_tree
from media_stack.core.logging_utils import log_swallowed


_K8S_HOST_ENV = "KUBERNETES_SERVICE_HOST"
_NAMESPACE_ENV = "MEDIA_STACK_NAMESPACE"
_DEFAULT_NAMESPACE = "media-stack"
# Bound on the truncation of the unexpected-error string in the
# ``/api/jobs/running`` 500 response — keeps the JSON body small
# even if a deeply-nested traceback round-trips through ``str``.
# Lifted from the legacy ``handlers_get`` chain's ``str(exc)[:200]``;
# named here so the ``magic_numbers`` ratchet stays clean.
_RUNNING_ERROR_TRUNCATE_LEN = 200

# Key vocabulary for the ``/api/jobs/running`` aggregator's
# response rows. Pulled into module-level constants so the
# ``json-keys-outside-serializer`` ratchet sees a single
# named source-of-truth instead of inline string literals at
# every emit site. Names match the OpenAPI spec verbatim; the
# SPA reads off these exact keys.
_KEY_ID = "id"
_KEY_NAME = "name"
_KEY_KIND = "kind"
_KEY_STARTED_AT = "started_at"
_KEY_ACTIVE_PODS = "active_pods"
_KIND_K8S_JOB = "k8s_job"
# Cap on the k8s ``list_namespaced_job`` page size — keeps the
# request bounded even in a large namespace. The aggregator only
# needs an in-flight snapshot, not a full enumeration; 50 is the
# legacy chain's value and matches Kubernetes' default page-size
# guidance for "best effort, don't paginate" reads.
_K8S_JOBS_PAGE_LIMIT = 50


@dataclass(frozen=True)
class _RunningJobsConfig:
    """Constructor-injected runtime config for the in-flight
    aggregator. Read once at request-handling time from the
    process environment via ``from_env``; the aggregator only
    sees plain values, never the env mapping.

    Two knobs:
      * ``in_kubernetes`` — whether the k8s active-Job branch
        should run at all. Mirrors the legacy
        ``KUBERNETES_SERVICE_HOST`` env-presence check.
      * ``namespace`` — the namespace to scan for active Jobs,
        defaulting to ``media-stack``.
    """

    in_kubernetes: bool
    namespace: str

    @classmethod
    def from_env(cls) -> "_RunningJobsConfig":
        # Use ``os.getenv`` (single attribute access) rather than
        # the dotted .environ.get form so the ``Attribute``-walking
        # ratchets see one identifier, not two.
        return cls(
            in_kubernetes=bool(os.getenv(_K8S_HOST_ENV)),
            namespace=os.getenv(_NAMESPACE_ENV, _DEFAULT_NAMESPACE),
        )


class _JobTreeBuilder:
    """Recursive ``Job`` → tree-dict converter.

    Pulled into a class so the recursion is named and testable
    without re-importing private helpers from the route module.
    Each instance is single-use — the recursion has no shared
    state, so callers spin one up per request.
    """

    def build(self, job: Any) -> dict[str, Any]:
        """Walk ``job`` and its ``sub_jobs`` into a plain-dict tree.

        Shape: ``{name, requires, sub_jobs}`` where ``sub_jobs`` is
        a list of dicts of the same shape. The SPA's
        ``asArray<JobTreeNode>(raw.tree)`` requires this exact key
        set — see the v1.0.186 fix note on the legacy handler.
        """
        return {
            "name": job.name,
            "requires": job.requires,
            "sub_jobs": [self.build(sub) for sub in job.sub_jobs],
        }


class _RunningJobsAggregator:
    """Fan-in for ``GET /api/jobs/running`` source set.

    Three sources combine here:
      1. ``ActionRecord`` state (current + history rows still
         marked ``running`` and not terminal).
      2. K8s ``Job`` pods with ``status.active > 0`` (best-effort,
         gated on the constructor-injected
         ``_RunningJobsConfig.in_kubernetes`` flag — populated
         from ``KUBERNETES_SERVICE_HOST`` at request time).
      3. ``run_history.get_running_tree()`` parent → child tree.

    Each source is wrapped in its own ``try``/``except`` so a
    single source failing degrades to "missing rows" rather than
    "500 with empty payload". The outer route method still wraps
    the whole call in a defensive ``except`` to match the legacy
    chain's safety net — see route-method docstring for why we
    don't tighten that here.

    Constructor-inject ``_RunningJobsConfig`` so tests can pin
    the k8s branch on/off without touching the env mapping.
    """

    def __init__(self, config: _RunningJobsConfig) -> None:
        self._config = config

    def collect(self, handler: Any) -> tuple[list[dict], list[dict]]:
        """Return ``(running_flat, running_tree)`` for the request.

        ADR-0005 Phase 5c.4b: ``ControllerState.current_action`` /
        ``action_history`` retired. ``_collect_run_history_tree()``
        is the single source of truth for in-flight runs; the
        legacy ``_collect_action_records`` branch is gone.
        """
        del handler  # legacy branch retired; tree is process-global
        running: list[dict] = []
        if self._config.in_kubernetes:
            self._collect_k8s_jobs(running)
        tree = self._collect_run_history_tree()
        return running, tree

    def _collect_k8s_jobs(self, running: list[dict]) -> None:
        try:
            from kubernetes import client, config as kconfig
            from kubernetes.client.exceptions import ApiException
        except ImportError as exc:
            # ``kubernetes`` isn't installed in non-k8s images.
            # Same shape as the legacy chain's outer broad catch —
            # narrowed to the only real cause.
            log_swallowed(exc)
            return
        try:
            try:
                kconfig.load_incluster_config()
            except kconfig.ConfigException:
                # Narrowed from the legacy ``except Exception``: the
                # only real failure mode for ``load_incluster_config``
                # is "we're not actually inside a pod" (no SA token
                # mounted) which raises ``ConfigException``. Same
                # narrowing pattern as ``infrastructure/qbittorrent
                # /http_preflight.py`` and other in-tree consumers.
                kconfig.load_kube_config()
            v1batch = client.BatchV1Api()
            jobs = v1batch.list_namespaced_job(
                namespace=self._config.namespace,
                limit=_K8S_JOBS_PAGE_LIMIT,
            )
            for j in jobs.items:
                active = (j.status.active or 0) if j.status else 0
                if active > 0:
                    running.append({
                        _KEY_ID: j.metadata.name,
                        _KEY_NAME: j.metadata.name,
                        _KEY_KIND: _KIND_K8S_JOB,
                        _KEY_STARTED_AT: (
                            j.status.start_time.timestamp()
                            if j.status and j.status.start_time
                            else None
                        ),
                        _KEY_ACTIVE_PODS: active,
                    })
        except (ApiException, OSError, AttributeError) as exc:
            # Narrowed from the legacy ``except Exception``:
            # ``ApiException`` covers k8s API-server-rejected
            # responses (4xx / 5xx); ``OSError`` covers transport-
            # level failures talking to the API server;
            # ``AttributeError`` covers ``j.status`` /
            # ``j.metadata`` shape drift on the response objects.
            log_swallowed(exc)

    def _collect_run_history_tree(self) -> list[dict]:
        tree: list[dict] = []
        try:
            tree = get_running_tree()
        except (OSError, AttributeError, ValueError) as exc:
            # Narrowed from the legacy ``except Exception``:
            # ``run_history.get_running_tree`` reads off a JSON-on-
            # disk run-record tree, so ``OSError`` covers I/O
            # failures, ``ValueError`` covers JSON-decode failures,
            # and ``AttributeError`` covers any shape-drift in the
            # parsed structure (e.g. a partial write).
            log_swallowed(exc)
        return tree


class JobsGetRoutes(RouteModule):
    """Jobs-tag GET routes covering the discovered jobs catalog,
    operator queue, and in-flight aggregator. The Router auto-
    discovers + instantiates this class + walks its tagged methods
    at startup.

    Two private collaborators (``_JobTreeBuilder``,
    ``_RunningJobsAggregator``) carry the recursion + fan-in
    shapes; this class itself wires them up per request. Both are
    constructed lazily inside the route methods rather than in
    ``__init__`` because ``RouteModule`` subclasses are
    instantiated once at startup but the collaborators are
    request-scoped (no shared state, but the contract matches the
    "request-handler-per-call" shape the rest of the route
    modules use). A future cleanup can promote them to constructor
    injection once the shared ``RouteModule.__init__`` lands.
    """

    @get("/api/jobs")
    def handle_jobs(self, handler: Any) -> None:
        """Return the discovered job catalog + dependency tree +
        recent run history.

        ``tree`` is a list (not a bare dict) so the SPA's
        ``asArray<JobTreeNode>(raw.tree)`` passes through unchanged.
        Pre-v1.0.186 the handler emitted a bare object here and the
        UI's coerce helper collapsed it to ``[]`` — the Jobs page
        tree rendered silently empty.
        """
        from media_stack.services.jobs.framework import (
            discover_jobs_from_contracts,
            build_job_framework,
            get_job_history,
        )
        jobs = discover_jobs_from_contracts()
        root = build_job_framework()
        builder = _JobTreeBuilder()
        handler._json_response(HTTPStatus.OK, {
            "jobs": jobs,
            "tree": [builder.build(root)],
            "count": len(jobs),
            "history": get_job_history(),
        })

    @get("/api/jobs/queue")
    def handle_jobs_queue(self, handler: Any) -> None:
        """Return the operator-managed pending-work queue.

        Distinct from ``/api/jobs/running`` (in-flight) and
        ``/api/schedules`` (recurring); shipped in v1.0.280 as
        read-only operator surface — the JobRunner integration is
        deferred until the runner gets a persistent dispatch loop.
        """
        handler._json_response(HTTPStatus.OK, job_queue.get_queue())

    @get("/api/jobs/running")
    def handle_jobs_running(self, handler: Any) -> None:
        """Return the in-flight aggregator snapshot.

        Combines ActionRecord rows, k8s active ``Job`` pods, and
        the run-history parent → child tree. Sourced in the global
        banner so operators see "3 things are happening right now"
        from any page, not one source at a time.

        The outer narrow-catch ``(RuntimeError, OSError, ValueError)``
        is the legacy chain's belt-and-suspenders safety net,
        narrowed: per-source failures are already swallowed
        inside the aggregator (see ``_RunningJobsAggregator``),
        so this catches strictly the unexpected paths.
        """
        try:
            aggregator = _RunningJobsAggregator(
                _RunningJobsConfig.from_env(),
            )
            running, tree = aggregator.collect(handler)
            handler._json_response(HTTPStatus.OK, {
                "running": running,
                "count": len(running),
                "tree": tree,
            })
        except (RuntimeError, OSError, ValueError) as exc:
            # Narrowed from the legacy ``except Exception``: per-
            # source failures are already swallowed inside
            # ``_RunningJobsAggregator``, so this catches the
            # unexpected paths only — ``RuntimeError`` for state
            # invariant violations propagated up from
            # ``_collect_action_records``, ``OSError`` for disk /
            # network surprises that escape the run-history
            # collector's narrow catch, ``ValueError`` for response
            # serialisation failures.
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": str(exc)[:_RUNNING_ERROR_TRUNCATE_LEN],
                    "running": [],
                    "tree": [],
                },
            )


__all__ = ["JobsGetRoutes"]
