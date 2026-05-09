"""Ratchet for ADR-0003 Phase 4a — promise dispatch resolution.

Asserts the structural contract that the Phase 4b orchestrator will
need at runtime:

  1. **Lifecycle entries resolve.** Every promise whose probe or
     ensurer is ``type: lifecycle`` names a service that exists in
     ``contracts/services/<id>.yaml`` AND that contract names a
     ``plugin.lifecycle_class`` AND the named class IS a
     ``ServiceLifecycle`` AND the named method exists on the class.
  2. **No depends_on dangles.** Every ``depends_on: [...]`` entry
     references an actual promise id.
  3. **No depends_on cycles.** Topological sort succeeds without
     hitting a cycle. (Phase 4b's orchestrator does the topo sort
     at runtime; failing fast at test time means a typo in YAML
     fails CI rather than the orchestrator at boot.)

The existing ``test_promises_registry.py`` already validates string
ensurers (legacy schema) — this ratchet adds the validation for the
new lifecycle schema. Both ratchets coexist; both must pass.
"""

from __future__ import annotations

import importlib
import unittest
from pathlib import Path

import yaml

from media_stack.domain.services.lifecycle import ServiceLifecycle
from media_stack.domain.services.promises import (
    LifecycleProbe,
    Promise,
)
from media_stack.infrastructure.promises.registry import load_registry


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS_SERVICES = _REPO_ROOT / "contracts" / "services"
_SKIP_SERVICE_FILES = frozenset({"_template.yaml", "_core.yaml", "core.yaml"})


def _load_service_lifecycle_class_paths() -> dict[str, str]:
    """Map ``service.id`` → ``plugin.lifecycle_class`` (dotted path).

    Skips service YAMLs that don't declare a lifecycle (the
    permissive ratchet in ``test_service_lifecycle_ratchet.py``
    enforces presence on Phase-2/3 services). Promises that
    reference a service without a lifecycle still error here —
    the resolution can't proceed.
    """
    out: dict[str, str] = {}
    for path in sorted(_CONTRACTS_SERVICES.glob("*.yaml")):
        if path.name in _SKIP_SERVICE_FILES:
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        sid = ((data.get("service") or {}).get("id") or "").strip()
        dotted = ((data.get("plugin") or {}).get("lifecycle_class") or "").strip()
        if sid:
            out[sid] = dotted  # may be empty; resolver handles that
    return out


def _resolve_lifecycle_instance(dotted: str, service_id: str):
    """Same resolution shape as the Phase-2 lifecycle ratchet — try
    parameterized, fall back to no-arg."""
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


class PromiseDispatchResolutionRatchet(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.promises: list[Promise] = load_registry()
        cls.service_lifecycles = _load_service_lifecycle_class_paths()

    # -----------------------------------------------------------------
    # Lifecycle probe + ensurer resolution
    # -----------------------------------------------------------------

    def test_lifecycle_probes_resolve_to_real_methods(self) -> None:
        offenders: list[str] = []
        for p in self.promises:
            if not isinstance(p.probe, LifecycleProbe):
                continue
            sid = p.probe.service
            method = p.probe.method
            offenders.extend(self._resolve_lifecycle_method(p.id, sid, method, "probe"))
        self.assertEqual(
            offenders, [],
            "Lifecycle probe resolution failed:\n" + "\n".join(offenders),
        )

    # ADR-0010 Phase 7 retired ``LifecycleEnsurer``; the
    # ``test_lifecycle_ensurers_resolve_to_real_methods`` test that
    # lived here is no longer applicable. Equivalent invariant for
    # JobEnsurers (handler module:callable resolves) is enforced by
    # the contract loader's handler-resolution at boot.

    def _resolve_lifecycle_method(
        self, pid: str, sid: str, method: str, role: str,
    ) -> list[str]:
        """Returns a list of error strings (empty when everything resolves)."""
        if not sid:
            return [f"  {pid}: {role} ``service`` is empty"]
        if not method:
            return [f"  {pid}: {role} ``method`` is empty"]
        dotted = self.service_lifecycles.get(sid)
        if dotted is None:
            return [
                f"  {pid}: {role} references unknown service {sid!r} "
                f"(no contracts/services/{sid}.yaml found)"
            ]
        if not dotted:
            return [
                f"  {pid}: {role} references service {sid!r} but its "
                "contract YAML doesn't declare plugin.lifecycle_class"
            ]
        try:
            instance = _resolve_lifecycle_instance(dotted, sid)
        except (ImportError, AttributeError, ValueError) as exc:
            return [f"  {pid}: {role} lifecycle_class {dotted!r} unresolvable: {exc}"]
        if not isinstance(instance, ServiceLifecycle):
            return [
                f"  {pid}: {role} {dotted!r} does not satisfy "
                "ServiceLifecycle Protocol"
            ]
        if not hasattr(instance, method):
            return [
                f"  {pid}: {role} method {method!r} does not exist on "
                f"{dotted}"
            ]
        if not callable(getattr(instance, method)):
            return [f"  {pid}: {role} attribute {method!r} on {dotted} is not callable"]
        return []

    # -----------------------------------------------------------------
    # depends_on graph integrity
    # -----------------------------------------------------------------

    def test_depends_on_references_existing_promises(self) -> None:
        all_ids = {p.id for p in self.promises}
        offenders: list[str] = []
        for p in self.promises:
            for dep in p.depends_on:
                if dep not in all_ids:
                    offenders.append(
                        f"  {p.id}: depends_on={dep!r} is not a known promise id"
                    )
        self.assertEqual(
            offenders, [],
            "depends_on references unknown promises:\n" + "\n".join(offenders),
        )

    def test_depends_on_has_no_cycles(self) -> None:
        # Kahn's algorithm — same logic the Phase 4b orchestrator
        # will use. Failing at test time turns "orchestrator hangs at
        # boot" into "CI fails on the bad PR".
        graph: dict[str, set[str]] = {p.id: set(p.depends_on) for p in self.promises}
        in_degree: dict[str, int] = {pid: 0 for pid in graph}
        for pid, deps in graph.items():
            for dep in deps:
                in_degree[pid] = in_degree.get(pid, 0)  # ensure key
        # Reverse: count how many promises depend on each promise
        reverse: dict[str, set[str]] = {pid: set() for pid in graph}
        for pid, deps in graph.items():
            for dep in deps:
                reverse.setdefault(dep, set()).add(pid)
        # Compute in-degrees (count of unresolved deps per promise)
        unresolved = {pid: len(deps) for pid, deps in graph.items()}
        ready = [pid for pid, d in unresolved.items() if d == 0]
        sorted_count = 0
        while ready:
            current = ready.pop()
            sorted_count += 1
            for dependent in reverse.get(current, ()):
                unresolved[dependent] -= 1
                if unresolved[dependent] == 0:
                    ready.append(dependent)
        if sorted_count != len(graph):
            stuck = [pid for pid, d in unresolved.items() if d > 0]
            self.fail(
                "depends_on cycle detected (or unresolvable graph). "
                f"Stuck promises: {sorted(stuck)}",
            )


if __name__ == "__main__":
    unittest.main()
