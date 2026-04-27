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
"""

from __future__ import annotations

from typing import Any


def build_compose_service_index(client: Any) -> dict[str, Any]:
    """Return ``{compose_service_name: Container}`` for every
    container the daemon currently manages, keyed by the canonical
    ``com.docker.compose.service`` label."""
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
        svc = str(labels.get("com.docker.compose.service") or "").strip()
        if svc and svc not in out:
            out[svc] = container
    return out


def resolve_compose_container(
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
    """
    index = (
        label_index
        if label_index is not None
        else build_compose_service_index(client)
    )
    cand = index.get(service_id)
    if cand is not None:
        return cand
    prefixed = index.get(f"media-stack-{service_id}")
    if prefixed is not None:
        return prefixed
    try:
        return client.containers.get(service_id)
    except Exception:  # noqa: BLE001
        try:
            return client.containers.get(f"media-stack-{service_id}")
        except Exception:  # noqa: BLE001
            return None
