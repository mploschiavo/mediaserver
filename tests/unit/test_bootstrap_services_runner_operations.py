import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.runner_operations_service import RunnerOperationRegistry  # noqa: E402


class RunnerOperationRegistryTests(unittest.TestCase):
    def test_invoke_with_enum(self):
        prewarm_op = "ensure_jellyfin_prewarm"
        registry = RunnerOperationRegistry(
            handlers={
                prewarm_op: lambda cfg, root, timeout: (
                    cfg,
                    root,
                    timeout,
                )
            }
        )
        result = registry.invoke(
            prewarm_op,
            {"jellyfin": {}},
            "/srv-config",
            60,
        )
        self.assertEqual(result, ({"jellyfin": {}}, "/srv-config", 60))

    def test_invoke_unknown_operation_raises(self):
        registry = RunnerOperationRegistry(handlers={})
        with self.assertRaises(KeyError):
            registry.invoke("missing-op")

    def test_from_maps_loads_handler_from_spec(self):
        registry = RunnerOperationRegistry.from_maps(
            handlers={},
            handler_specs={"ceil_op": "math:ceil"},
        )
        self.assertEqual(registry.invoke("ceil_op", 1.2), 2)

    def test_from_maps_disables_handler_when_spec_empty(self):
        registry = RunnerOperationRegistry.from_maps(
            handlers={"example": lambda: "ok"},
            handler_specs={"example": ""},
        )
        with self.assertRaises(KeyError):
            registry.invoke("example")

    def test_from_maps_rejects_invalid_handler_spec(self):
        with self.assertRaises(ValueError):
            RunnerOperationRegistry.from_maps(
                handlers={},
                handler_specs={"bad": "missing_colon_spec"},
            )


if __name__ == "__main__":
    unittest.main()
