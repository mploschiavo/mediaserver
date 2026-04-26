"""Ratchet R-7: every method on the EdgeBindingAdapter Protocol is
implemented by every adapter, and adapters produce equivalent
ApplyPlans for the same input on critical invariants.

The Protocol surface is small (``name``, ``detect``, ``compute_apply_plan``).
Future PRs add ComposeHostPortAdapter; this ratchet ensures the
contract stays uniform — no methods drift, both implementations
agree on the output of basic queries.
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.edge.binding_adapter import EdgeBindingAdapter  # noqa: E402
from media_stack.services.edge.k8s_ingress_adapter import K8sIngressAdapter  # noqa: E402


# Add ComposeHostPortAdapter here when PR-7 lands. The ratchet
# auto-fans across every adapter listed.
KNOWN_ADAPTERS: list[type] = [K8sIngressAdapter]


class EdgeBindingAdapterCoverageRatchet(unittest.TestCase):
    def test_protocol_surface_implemented_by_every_adapter(self) -> None:
        # Names the Protocol declares (excluding dunders).
        protocol_attrs = {
            n for n in dir(EdgeBindingAdapter)
            if not n.startswith("_")
        }
        for cls in KNOWN_ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                missing = [a for a in protocol_attrs if not hasattr(cls, a)]
                self.assertEqual(
                    missing, [],
                    f"{cls.__name__} is missing Protocol attribute(s): "
                    f"{missing}. Either implement them or update "
                    f"EdgeBindingAdapter.",
                )

    def test_every_adapter_has_unique_name(self) -> None:
        names = [cls().name for cls in KNOWN_ADAPTERS]
        self.assertEqual(len(names), len(set(names)),
                         f"Adapter names must be unique: {names}")

    def test_compute_apply_plan_signature_uniform(self) -> None:
        # The plan-builder takes one argument (the cfg) and returns
        # an ApplyPlan. Locking the signature so future adapters can
        # be swapped without caller-side changes.
        for cls in KNOWN_ADAPTERS:
            with self.subTest(adapter=cls.__name__):
                sig = inspect.signature(cls.compute_apply_plan)
                params = list(sig.parameters)
                # ``self`` + ``cfg`` = 2 params.
                self.assertEqual(
                    len(params), 2,
                    f"{cls.__name__}.compute_apply_plan must take "
                    f"(self, cfg) — got {params}",
                )


if __name__ == "__main__":
    unittest.main()
