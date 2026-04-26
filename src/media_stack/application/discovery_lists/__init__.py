"""Discovery-list application-layer use cases.

ADR-0002 Phase 16-E (discovery_lists) — orchestrates arr import-list
schema reconciliation, payload construction, and kickoff command
sequencing. Depends on domain/discovery_lists/* and arr-app config
models. No raw HTTP — wraps an injected service callable.
"""

from .ops import (
    build_arr_import_list_payload,
    ensure_arr_discovery_lists_for_app,
    resolve_import_list_definitions,
    trigger_arr_discovery_kickoff,
)

__all__ = [
    "resolve_import_list_definitions",
    "build_arr_import_list_payload",
    "ensure_arr_discovery_lists_for_app",
    "trigger_arr_discovery_kickoff",
]
