"""Compose-aware container lookup helpers.

Service IDs in the SERVICES registry (``controller``, ``ui``,
``grabit``, ``jdownloader``, ``authentik``, ``mythtv``...) do NOT
always match docker container names. Compose lets each service set
``container_name`` independently, and many of our services use
prefixed names (``media-stack-controller``, ``media-stack-ui``).
Other service IDs in the registry refer to optional services that
aren't deployed in the active compose profile.

Looking up by raw service_id therefore floods the controller log
with ``404 Client Error ... No such container`` for every probe
cycle. This module centralises the resolver:

  1. Try the canonical ``com.docker.compose.service`` label.
  2. Fall back to ``media-stack-<service_id>`` prefix.
  3. Fall back to the raw ``service_id`` as the docker name.
  4. Return ``None`` on all-misses — callers treat that as
     "service not deployed in this profile" without raising.

Extracted from ``api.services.workload_inspector`` so the same
fallback applies across every consumer (logs, restart, healthcheck,
crashloop classifier).

ADR-0012: top-level ``FunctionDef`` count must stay at zero. The
helpers are bundled on ``DockerResolver`` and re-exported as
module-level aliases so every existing
``from media_stack.core.docker_resolver import resolve_compose_container``
keeps working with the same signature.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "DockerResolver",
    "build_compose_service_index",
    "resolve_compose_container",
]


# Canonical compose label that ``docker compose`` writes onto every
# container it manages. This is the only authoritative mapping from
# "service-id in compose.yaml" to "live container"; any other route
# (raw container_name, prefix-stripping, etc.) is a fallback.
_COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

# Container names in this stack are prefixed ``media-stack-<service>``
# whenever the compose file overrides ``container_name``. Keeping the
# prefix as a module constant means a future rename (e.g. when a
# secondary stack lands) only changes one literal.
_CONTAINER_NAME_PREFIX = "media-stack-"


class DockerResolver:
    """Compose-aware ``service_id`` to container resolver.

    Plain instance methods — no ``@staticmethod`` — so the class
    is a legitimate dispatch surface per ADR-0012. Module-level
    aliases below preserve the original free-function names so
    callers keep importing
    ``build_compose_service_index`` / ``resolve_compose_container``
    without churn.

    The class holds no state — every call is parameterised by the
    ``client`` argument — so a single module-level instance
    (``_INSTANCE``) suffices. We deliberately avoid binding the
    docker client at construction time because the same controller
    process can resolve against multiple daemons (local socket
    vs. a remote ``DOCKER_HOST``) within the same probe cycle.
    """

    def build_compose_service_index(self, client: Any) -> dict[str, Any]:
        """Return ``{compose_service_name: Container}`` for every
        container the daemon currently manages, keyed by the canonical
        ``com.docker.compose.service`` label.

        Why this exists as its own call: in a hot loop (the auto-heal
        sweep, the health probe cycle) callers resolve dozens of
        services per pass. Doing one ``containers.list(all=True)``
        per cycle and reusing the index is O(N+M) instead of O(N*M).

        Args:
            client: A docker SDK client (or compatible duck-type)
                exposing ``containers.list(all=True)`` returning an
                iterable of container objects with a ``labels`` dict
                and/or ``attrs["Config"]["Labels"]`` fallback.

        Returns:
            ``{compose_service_name: Container}`` for every container
            with a ``com.docker.compose.service`` label. Empty dict on
            any daemon error — callers treat that as "no managed
            containers" rather than crashing the probe cycle.
        """
        try:
            containers = list(client.containers.list(all=True))
        except Exception:  # noqa: BLE001
            return {}
        out: dict[str, Any] = {}
        for container in containers:
            try:
                labels = (
                    getattr(container, "labels", None)
                    or (getattr(container, "attrs", {}) or {})
                    .get("Config", {}).get("Labels", {})
                    or {}
                )
            except Exception:  # noqa: BLE001
                labels = {}
            svc = str(labels.get(_COMPOSE_SERVICE_LABEL) or "").strip()
            if svc and svc not in out:
                out[svc] = container
        return out

    def resolve_compose_container(
        self,
        client: Any,
        service_id: str,
        *,
        label_index: dict[str, Any] | None = None,
    ) -> Any | None:
        """Map a service-id to a live container, trying:

        1. The labelled lookup (compose-managed containers).
        2. The compose ``media-stack-`` prefix (``controller`` →
           ``media-stack-controller``).
        3. The raw service_id as a literal container name.

        Pass ``label_index`` when calling in a hot loop so the
        list-all probe runs once per cycle instead of per-service.

        Returns ``None`` on all-misses — never raises a 404 into the
        audit log; callers treat ``None`` as 'not deployed in this
        profile' and emit a structured 'unknown' / 'skipped' status
        instead.

        Args:
            client: A docker SDK client (see
                :meth:`build_compose_service_index`).
            service_id: The registry service identifier
                (``controller``, ``ui``, …).
            label_index: Optional pre-built index. When ``None`` we
                build one ourselves; supply a shared index when
                resolving many services in a tight loop.

        Returns:
            The container object on hit, ``None`` on all-miss.
        """
        index = (
            label_index
            if label_index is not None
            else self.build_compose_service_index(client)
        )
        cand = index.get(service_id)
        if cand is not None:
            return cand
        prefixed_key = _CONTAINER_NAME_PREFIX + service_id
        prefixed = index.get(prefixed_key)
        if prefixed is not None:
            return prefixed
        try:
            return client.containers.get(service_id)
        except Exception:  # noqa: BLE001
            try:
                return client.containers.get(prefixed_key)
            except Exception:  # noqa: BLE001
                return None


_INSTANCE = DockerResolver()


# Module-level aliases. These preserve the legacy free-function
# import surface — every existing
# ``from media_stack.core.docker_resolver import resolve_compose_container``
# call site keeps working unchanged. No test in the current tree
# ``mock.patch``es these names; if a future test does, swap the
# bound-method capture for a lambda dispatching through
# ``sys.modules[__name__]`` so the patch wins (per ADR-0012).
build_compose_service_index = _INSTANCE.build_compose_service_index
resolve_compose_container = _INSTANCE.resolve_compose_container
