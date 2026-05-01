"""Permissive ServiceLifecycle ratchet — ADR-0003 Phase 2.

Asserts the contract: when a service YAML names a
``plugin.lifecycle_class``, that class MUST exist and MUST satisfy
``ServiceLifecycle`` (i.e. ``isinstance(impl, ServiceLifecycle)``
returns True).

**Permissive on purpose.** Phase 2 only ships ``JellyfinLifecycle``
and ``ServarrLifecycle`` (sonarr/radarr/lidarr/readarr/prowlarr) — the
other 23 services don't have lifecycle implementations yet, so this
ratchet does NOT require ``lifecycle_class`` to be present. Phase 3
adds the rest of the implementations; Phase 5 tightens the ratchet to
"every service YAML MUST name a lifecycle class".

The expected count below is the floor: it ratchets UPWARD as Phase 3
adds implementations, never downward. A regression that drops the
``lifecycle_class`` field from a YAML or breaks an implementation's
Protocol conformance will fail this test.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

import yaml

from media_stack.domain.services import ServiceLifecycle


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS_DIR = _REPO_ROOT / "contracts" / "services"

# Skipped — these are templates / shared schemas, not real services.
_SKIP_FILES = frozenset({"_template.yaml", "_core.yaml", "core.yaml"})


def _load_service_yamls() -> dict[str, dict]:
    """Map ``service.id`` → contract dict, skipping templates/shared."""
    out: dict[str, dict] = {}
    for path in sorted(_CONTRACTS_DIR.glob("*.yaml")):
        if path.name in _SKIP_FILES:
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        sid = ((data.get("service") or {}).get("id") or "").strip()
        if not sid:
            continue
        out[sid] = data
    return out


def _resolve_class(dotted: str):
    """Resolve ``module.path:ClassName`` → class object."""
    if ":" not in dotted:
        raise ValueError(
            f"lifecycle_class must be 'module.path:ClassName', got {dotted!r}",
        )
    mod_path, cls_name = dotted.split(":", 1)
    mod = importlib.import_module(mod_path)
    return getattr(mod, cls_name)


class ServiceLifecycleRatchet(unittest.TestCase):
    def test_named_lifecycle_classes_exist_and_satisfy_protocol(self) -> None:
        """Every YAML that names a lifecycle class — that class MUST
        be importable AND instantiable AND pass the runtime
        ``isinstance(impl, ServiceLifecycle)`` check."""
        offenders: list[str] = []
        for service_id, data in _load_service_yamls().items():
            plugin = data.get("plugin") or {}
            dotted = (plugin.get("lifecycle_class") or "").strip()
            if not dotted:
                continue  # permissive: not yet required for all services

            try:
                cls = _resolve_class(dotted)
            except (ImportError, AttributeError, ValueError) as exc:
                offenders.append(
                    f"  {service_id}: lifecycle_class={dotted!r} not "
                    f"resolvable: {exc}",
                )
                continue

            # ServarrLifecycle takes service_id; JellyfinLifecycle
            # takes nothing. Try the parameterized form first (it's
            # the harder constraint), fall back to no-arg.
            instance = None
            for kwargs in ({"service_id": service_id}, {}):
                try:
                    instance = cls(**kwargs)
                    break
                except TypeError:
                    continue
                except Exception as exc:  # noqa: BLE001
                    offenders.append(
                        f"  {service_id}: instantiating {dotted} "
                        f"with {kwargs} raised: {exc}",
                    )
                    instance = None
                    break

            if instance is None:
                offenders.append(
                    f"  {service_id}: could not instantiate {dotted} "
                    "with either ()  or (service_id=...)",
                )
                continue

            if not isinstance(instance, ServiceLifecycle):
                offenders.append(
                    f"  {service_id}: {dotted} does not satisfy "
                    "ServiceLifecycle (missing one or more required "
                    "methods)",
                )

        self.assertEqual(
            offenders, [],
            "ServiceLifecycle ratchet failed:\n" + "\n".join(offenders),
        )

    # --- floor: services KNOWN to have lifecycles in this slice ----
    #
    # Phase 2 commits Jellyfin + sonarr/radarr/lidarr/readarr/prowlarr.
    # If any of these loses its lifecycle_class field, the count drops
    # and this test fails. Tightens upward as Phase 3 adds more.
    EXPECTED_LIFECYCLE_FLOOR = 8

    def test_lifecycle_floor_does_not_regress(self) -> None:
        actual = sum(
            1
            for data in _load_service_yamls().values()
            if (data.get("plugin") or {}).get("lifecycle_class")
        )
        self.assertGreaterEqual(
            actual, self.EXPECTED_LIFECYCLE_FLOOR,
            f"Service-lifecycle floor regressed: {actual} services declare "
            f"lifecycle_class, expected at least "
            f"{self.EXPECTED_LIFECYCLE_FLOOR}. Did a recent change drop the "
            "field from a contract YAML?",
        )

    # --- explicit Phase-2 coverage check ---------------------------

    PHASE_2_SERVICES = (
        "jellyfin", "sonarr", "radarr", "lidarr", "readarr", "prowlarr",
    )

    def test_phase_2_services_each_name_a_lifecycle_class(self) -> None:
        yamls = _load_service_yamls()
        missing: list[str] = []
        for sid in self.PHASE_2_SERVICES:
            data = yamls.get(sid)
            if data is None:
                missing.append(f"{sid}: contract YAML not found")
                continue
            dotted = ((data.get("plugin") or {}).get("lifecycle_class") or "").strip()
            if not dotted:
                missing.append(f"{sid}: plugin.lifecycle_class missing")
        self.assertEqual(
            missing, [],
            "Phase-2 services without lifecycle_class:\n" + "\n".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
