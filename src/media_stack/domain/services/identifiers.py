"""Phantom-typed identifiers for the service-lifecycle domain.

Without these, every "service id" / "ensurer method" / "user id" /
"job id" is a bare ``str`` and the type-checker can't distinguish a
``ServiceId`` from a ``UserId`` even though passing one where the
other is expected is a real bug. ``NewType`` is the cheapest
phantom-typing mechanism Python offers — the runtime is still
``str``, but mypy / pyright reject mismatched assignments at the
call site.

This module deliberately holds ONLY ``NewType`` declarations
(zero classes, zero functions) so it doesn't perturb the
class-structure or loose-function ratchets — a new module of pure
type aliases is the canonical shape.

ADR-0005 Phase 5b: ``LifecycleEnsurerInvoker`` is the first
caller. Future cutovers (auto-heal, orchestrator-tick instrumented
audit emitters, Jobs-framework dispatch) consume the same aliases.
"""

from __future__ import annotations

from typing import NewType


# Identifies a service in the controller's runtime registry —
# ``"sonarr"`` / ``"radarr"`` / ``"jellyfin"`` etc. These are
# string-keyed everywhere on the wire (path params, JSON bodies,
# YAML registry entries) so the runtime type stays ``str``.
ServiceId = NewType("ServiceId", str)

# Identifies a method on a ``ServiceLifecycle`` — e.g.
# ``"ensure_download_client"`` / ``"probe_running"``. Pairs with
# ``ServiceId`` to identify a single lifecycle ensurer in the
# promise registry.
EnsurerMethod = NewType("EnsurerMethod", str)

# Identifies the caller that triggered a manual ensurer dispatch —
# ``"operator"`` / ``"auto-heal"`` / ``"orchestrator-tick"``. The
# value is observability-side (audit log + SSE), not a security
# decision; bad values fall back to ``"operator"`` rather than
# rejecting the request.
InvocationSource = NewType("InvocationSource", str)


__all__ = [
    "EnsurerMethod",
    "InvocationSource",
    "ServiceId",
]
