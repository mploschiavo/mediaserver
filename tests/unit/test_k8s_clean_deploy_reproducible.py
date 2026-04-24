"""Ratchets for "clean K8s deploy = same working stack every time".

Every test here is a FAILING ratchet against a known past regression.
A passing suite means ``kubectl apply -f dist/k8s-deploy.yaml`` (or
``kubectl apply -k k8s/``) on an empty cluster produces a working
dashboard + routing without any operator dashboard interaction.

The incident this ratchet-set exists to prevent: before v1.0.169 the
controller silently fell back to ``.local`` LAN defaults when the
profile ConfigMap wasn't provided, ingress-config silently skipped
because routing looked empty, and "patches on a live system" became
the only way to reach a working state. Live patches aren't
reproducible; this invariant is.

Covered invariants
------------------
1. ``dist/k8s-deploy.yaml`` SHIPS the profile ConfigMap (not optional,
   not a commented-out template — actually present and populated).
2. The controller's profile volume is NOT ``optional: true`` — the
   controller must refuse to start rather than silently degrade when
   the ConfigMap is absent.
3. ``seed-runtime-overrides`` is registered in ``pre_bootstrap`` with
   a priority that runs it before anything that reads the merged
   routing view.
4. ``k8s_ingress_sync.reconcile()`` raises (rather than returning a
   silent skip) when routing lookup fails on K8s — so bootstrap
   surfaces the real cause in the dashboard, not a green checkmark.
5. ``k8s/kustomization.yaml`` wires the ``configMapGenerator`` so
   ``kustomize build`` yields the same ConfigMap the dist bundle ships.
"""

from __future__ import annotations

import os
import unittest
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_DIST = ROOT / "dist" / "k8s-deploy.yaml"
_K8S_CONTROLLER = ROOT / "k8s" / "controller.yaml"
_K8S_KUSTOMIZATION = ROOT / "k8s" / "kustomization.yaml"
_K8S_STANDARD_PROFILE = ROOT / "examples" / "bootstrap-profiles" / "media-k8s-standard.yaml"
_CORE_CONTRACT = ROOT / "contracts" / "services" / "core.yaml"


def _split_yaml_docs(text: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]


class DistShipsProfileConfigMapTests(unittest.TestCase):
    """The profile ConfigMap MUST be present in dist/k8s-deploy.yaml."""

    def setUp(self):
        self.docs = _split_yaml_docs(_DIST.read_text(encoding="utf-8"))

    def test_configmap_present(self):
        cms = [
            d for d in self.docs
            if d.get("kind") == "ConfigMap"
            and d.get("metadata", {}).get("name") == "media-stack-controller-profile"
        ]
        self.assertEqual(
            len(cms), 1,
            "media-stack-controller-profile ConfigMap must ship baked-in "
            "to dist/k8s-deploy.yaml (found {}). Clean deploys on a fresh "
            "cluster need the profile to avoid silently falling back to "
            "LAN defaults.".format(len(cms)),
        )

    def test_configmap_has_profile_yaml_with_gateway_host(self):
        cm = next(d for d in self.docs if d.get("kind") == "ConfigMap"
                  and d.get("metadata", {}).get("name") == "media-stack-controller-profile")
        data = (cm.get("data") or {}).get("profile.yaml") or ""
        self.assertTrue(data, "ConfigMap.data.profile.yaml must be non-empty")
        profile = yaml.safe_load(data) or {}
        routing = profile.get("routing") or {}
        self.assertTrue(
            str(routing.get("gateway_host") or "").strip(),
            "Baked-in profile must declare a gateway_host so ingress-config "
            "can build real rules on clean deploy. An empty gateway_host "
            "sends ingress-config down the fail-loud branch and bootstrap "
            "refuses to come up.",
        )

    def test_configmap_namespace_is_media_stack(self):
        cm = next(d for d in self.docs if d.get("kind") == "ConfigMap"
                  and d.get("metadata", {}).get("name") == "media-stack-controller-profile")
        self.assertEqual(
            cm.get("metadata", {}).get("namespace"), "media-stack",
            "ConfigMap namespace must match the Deployment's namespace.",
        )


class ProfileVolumeIsRequiredTests(unittest.TestCase):
    """The controller's profile volume must NOT be ``optional: true`` —
    absent ConfigMap → controller fails fast, never silent LAN fallback."""

    def _controller_profile_volume(self, text: str) -> dict:
        for doc in _split_yaml_docs(text):
            if (doc.get("kind") == "Deployment"
                    and doc.get("metadata", {}).get("name") == "media-stack-controller"):
                spec = doc["spec"]["template"]["spec"]
                for v in spec.get("volumes") or []:
                    if v.get("name") == "controller-profile":
                        return v
        raise AssertionError("controller-profile volume not found")

    def test_dist_profile_volume_not_optional(self):
        vol = self._controller_profile_volume(_DIST.read_text(encoding="utf-8"))
        cm = vol.get("configMap") or {}
        self.assertNotEqual(
            cm.get("optional"), True,
            "dist/k8s-deploy.yaml controller profile volume must NOT be "
            "optional — the ConfigMap ships baked-in, so absence is a "
            "real deploy error. ``optional: true`` hid months of broken "
            "clean deploys by silently falling back to LAN defaults.",
        )

    def test_k8s_source_profile_volume_not_optional(self):
        vol = self._controller_profile_volume(_K8S_CONTROLLER.read_text(encoding="utf-8"))
        cm = vol.get("configMap") or {}
        self.assertNotEqual(
            cm.get("optional"), True,
            "k8s/controller.yaml controller profile volume must NOT be "
            "optional (same reason as dist). The kustomization ships "
            "the ConfigMap via configMapGenerator.",
        )


class KustomizationShipsProfileTests(unittest.TestCase):
    """k8s/kustomization.yaml must have an ACTIVE configMapGenerator
    for the profile — not a commented-out template."""

    def test_configmap_generator_is_active(self):
        data = yaml.safe_load(_K8S_KUSTOMIZATION.read_text(encoding="utf-8"))
        generators = data.get("configMapGenerator") or []
        names = [g.get("name") for g in generators]
        self.assertIn(
            "media-stack-controller-profile", names,
            "k8s/kustomization.yaml must include the "
            "media-stack-controller-profile configMapGenerator so "
            "``kustomize build k8s/`` includes it on every apply. The "
            "earlier commented-out template pattern meant the operator "
            "had to uncomment and edit — clean-deploy reproducibility "
            "requires it to be the default.",
        )

    def test_configmap_generator_points_at_standard_profile(self):
        data = yaml.safe_load(_K8S_KUSTOMIZATION.read_text(encoding="utf-8"))
        gen = next((g for g in (data.get("configMapGenerator") or [])
                    if g.get("name") == "media-stack-controller-profile"), None)
        self.assertIsNotNone(gen, "configMapGenerator for profile not found")
        files = gen.get("files") or []
        found_standard = any(
            "media-k8s-standard.yaml" in str(f) for f in files
        )
        self.assertTrue(
            found_standard,
            "configMapGenerator must reference media-k8s-standard.yaml — "
            "that's the profile baked into dist/k8s-deploy.yaml, so "
            "``kustomize build`` and the dist bundle must agree on content.",
        )


class SeedRuntimeOverridesRegisteredTests(unittest.TestCase):
    """The seed-runtime-overrides job must run early in pre_bootstrap."""

    def setUp(self):
        doc = yaml.safe_load(_CORE_CONTRACT.read_text(encoding="utf-8")) or {}
        self.jobs = (doc.get("plugin") or {}).get("jobs") or {}

    def test_job_registered(self):
        self.assertIn(
            "seed-runtime-overrides", self.jobs,
            "seed-runtime-overrides job must be registered in "
            "contracts/services/core.yaml. Without it, clean deploys "
            "never get routing/auth-overrides files written from the "
            "profile — ingress-config silently skips on first run.",
        )

    def test_job_in_pre_bootstrap_phase(self):
        job = self.jobs["seed-runtime-overrides"]
        self.assertEqual(
            job.get("phase"), "pre_bootstrap",
            "seed-runtime-overrides must run in pre_bootstrap so the "
            "merged routing/auth view is complete before any downstream "
            "job reads it.",
        )

    def test_job_priority_runs_first(self):
        job = self.jobs["seed-runtime-overrides"]
        priority = int(job.get("priority", 0))
        discover_prio = int(self.jobs.get("discover-api-keys", {}).get("priority", 999))
        self.assertLess(
            priority, discover_prio,
            "seed-runtime-overrides must run BEFORE discover-api-keys "
            "(or any other pre_bootstrap job) so the overrides are on "
            "disk when the merged-view reads start.",
        )

    def test_handler_is_importable(self):
        import importlib
        handler = self.jobs["seed-runtime-overrides"].get("handler") or ""
        mod, func = handler.split(":", 1) if ":" in handler else (None, None)
        self.assertIsNotNone(mod, f"handler {handler!r} must be module:func")
        mod_obj = importlib.import_module(mod)
        self.assertTrue(
            hasattr(mod_obj, func),
            f"handler {handler!r} not found in module",
        )


class IngressConfigFailsLoudTests(unittest.TestCase):
    """``reconcile()`` on K8s must raise, not skip, when it can't
    produce rules. Silent skip was how clean-deploy reproducibility
    broke without any visible signal."""

    def test_raises_when_routing_empty_on_k8s(self):
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from media_stack.api.services import k8s_ingress_sync

        # Fake K8s context with an empty routing view. Monkey-patch
        # the module's routing loader so we don't need a real cluster
        # or a controller process. The test's point is: "given a K8s
        # deploy whose routing view came back empty, reconcile MUST
        # raise" — the fact that our implementation calls an external
        # helper is incidental.
        original = k8s_ingress_sync._routing_from_runtime
        k8s_ingress_sync._routing_from_runtime = lambda: {}
        original_ns = os.environ.get("K8S_NAMESPACE")
        os.environ["K8S_NAMESPACE"] = "media-stack"
        try:
            with self.assertRaises(RuntimeError) as ctx:
                k8s_ingress_sync.reconcile()
            self.assertIn(
                "routing config is empty", str(ctx.exception),
                "Failure message should name the root cause so the "
                "operator can find the fix fast (missing ConfigMap, "
                "profile read failure, etc.)",
            )
        finally:
            k8s_ingress_sync._routing_from_runtime = original
            if original_ns is None:
                os.environ.pop("K8S_NAMESPACE", None)
            else:
                os.environ["K8S_NAMESPACE"] = original_ns

    def test_compose_still_returns_skip(self):
        """Outside K8s (``K8S_NAMESPACE`` unset), reconcile is a
        legitimate no-op — compose has no Ingress layer to touch."""
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from media_stack.api.services import k8s_ingress_sync

        original_ns = os.environ.get("K8S_NAMESPACE")
        os.environ.pop("K8S_NAMESPACE", None)
        try:
            result = k8s_ingress_sync.reconcile()
            self.assertFalse(result.get("applied"))
            self.assertTrue(result.get("skipped"))
        finally:
            if original_ns is not None:
                os.environ["K8S_NAMESPACE"] = original_ns


class SeedRuntimeOverridesBehaviourTests(unittest.TestCase):
    """End-to-end: call the seed adapter against a tmp CONFIG_ROOT
    with the standard profile, assert both files exist afterwards."""

    def test_seeds_overrides_from_profile(self):
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from media_stack.services.apps.core.job_adapters import seed_runtime_overrides

        with tempfile.TemporaryDirectory() as tmp:
            original_root = os.environ.get("CONFIG_ROOT")
            original_profile = os.environ.get("BOOTSTRAP_PROFILE_FILE")
            os.environ["CONFIG_ROOT"] = tmp
            os.environ["BOOTSTRAP_PROFILE_FILE"] = str(_K8S_STANDARD_PROFILE)
            try:
                class _Ctx:
                    pass
                result = seed_runtime_overrides(_Ctx())
            finally:
                if original_root is None:
                    os.environ.pop("CONFIG_ROOT", None)
                else:
                    os.environ["CONFIG_ROOT"] = original_root
                if original_profile is None:
                    os.environ.pop("BOOTSTRAP_PROFILE_FILE", None)
                else:
                    os.environ["BOOTSTRAP_PROFILE_FILE"] = original_profile

            self.assertIn("routing-overrides.yaml", result.get("created") or [])
            self.assertIn("auth-overrides.yaml", result.get("created") or [])
            routing = Path(tmp) / ".controller" / "routing-overrides.yaml"
            auth = Path(tmp) / ".controller" / "auth-overrides.yaml"
            self.assertTrue(routing.is_file())
            self.assertTrue(auth.is_file())
            # File content parses and carries the profile's gateway_host.
            r = yaml.safe_load(routing.read_text(encoding="utf-8")) or {}
            self.assertTrue(
                (r.get("routing") or {}).get("gateway_host"),
                "Seeded routing-overrides must preserve the profile's "
                "gateway_host — this is the ONE value that must survive "
                "every clean deploy to reach the configured external host.",
            )


if __name__ == "__main__":
    unittest.main()
