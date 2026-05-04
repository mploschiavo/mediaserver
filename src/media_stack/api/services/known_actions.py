"""Accept-list of POST ``/actions/{name}`` action identifiers.

Lifted from ``media_stack.api.handlers_post`` during ADR-0007 Phase 2
Phase E (legacy-handler retirement).

The ``KNOWN_ACTIONS`` frozenset is the gate for ``POST /actions/{name}``:
any action name that isn't in the set returns 404. The set is built
at import time from three sources:

1. The hard-coded ``_CORE_ACTIONS`` (``bootstrap``, ``reconcile``,
   ``configure-media-server``).
2. Every job declared in a service contract (loaded via
   ``discover_jobs_from_contracts``).
3. Every alias declared in a contract (loaded via
   ``discover_job_aliases``). Aliases are first-class accept-list
   entries: ``POST /actions/reconcile`` returns 202, ``run_job``
   resolves the alias to ``bootstrap`` and walks the same tree.
   Without this merge, hitting the alias would 404 even though
   it's declared in the contract.
"""

from __future__ import annotations

from media_stack.core.logging_utils import log_swallowed


# bootstrap: the root of the tree (parent of every other job)
# configure-media-server: composite that runs every media-server
#   leaf job; parented in the tree as a phase group
# reconcile: re-runs the entire bootstrap pipeline; effectively
#   an alias for "bootstrap" with the cancel flag cleared
_CORE_ACTIONS = frozenset({
    "bootstrap", "reconcile", "configure-media-server",
})


class KnownActionsBuilder:
    """Materialises the ``KNOWN_ACTIONS`` accept-list.

    Stateless service. Method-form so callers can constructor-inject
    a stub for tests; the default path discovers jobs + aliases off
    the contract registry the same way the legacy
    ``PostRequestHandler._build_known_actions`` static method did.
    """

    def build(self) -> frozenset[str]:
        actions: set[str] = set(_CORE_ACTIONS)
        try:
            from media_stack.services.jobs.framework import (
                discover_jobs_from_contracts,
                discover_job_aliases,
            )
            for job in discover_jobs_from_contracts():
                actions.add(job["name"])
            for alias in discover_job_aliases():
                actions.add(alias)
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
        return frozenset(actions)


_KNOWN_ACTIONS_BUILDER = KnownActionsBuilder()
KNOWN_ACTIONS = _KNOWN_ACTIONS_BUILDER.build()


__all__ = [
    "_CORE_ACTIONS",
    "KNOWN_ACTIONS",
    "KnownActionsBuilder",
]
