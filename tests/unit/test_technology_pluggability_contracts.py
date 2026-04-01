import ast
import importlib
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PLUGIN_ROOT = ROOT / "scripts" / "bootstrap_defaults" / "plugins"

TECHNOLOGIES = [
    "jellyfin",
    "jellyseerr",
    "bazarr",
    "prowlarr",
    "qbittorrent",
    "sonarr",
    "radarr",
    "lidarr",
    "readarr",
    "sabnzbd",
    "tautulli",
    "flaresolverr",
    "maintainerr",
    "homepage",
]

MIN_REGISTRATION_REQUIREMENTS = {
    "jellyfin": {
        "adapter_classes": {"media_server"},
        "app_service_classes": {"jellyfin_livetv_service"},
        "operation_handlers": {"ensure_jellyfin_livetv"},
    },
    "jellyseerr": {
        "app_service_classes": {"request_manager_service"},
        "operation_handlers": {"configure_jellyseerr"},
    },
    "bazarr": {
        "app_service_classes": {"bazarr_service"},
        "operation_handlers": {"ensure_bazarr_arr_integration"},
    },
    "prowlarr": {
        "app_service_classes": {"prowlarr_service"},
        "operation_handlers": {"ensure_prowlarr_ready"},
    },
    "qbittorrent": {
        "adapter_classes": {"download_client"},
        "app_service_classes": {"torrent_client_service"},
        "operation_handlers": {"torrent_client_login", "setup_torrent_categories"},
    },
    "sonarr": {"adapter_classes": {"servarr"}},
    "radarr": {"adapter_classes": {"servarr"}},
    "lidarr": {"adapter_classes": {"servarr"}},
    "readarr": {"adapter_classes": {"servarr"}},
    "sabnzbd": {
        "adapter_classes": {"download_client"},
        "app_service_classes": {"usenet_client_service"},
        "operation_handlers": {
            "read_sabnzbd_api_key",
            "ensure_sabnzbd_defaults",
            "ensure_sabnzbd_categories",
        },
    },
    "tautulli": {"app_service_classes": {"tautulli_service"}},
    "flaresolverr": {},
    "maintainerr": {
        "app_service_classes": {"maintainerr_service"},
        "operation_handlers": {"ensure_maintainerr_policy", "ensure_maintainerr_integrations"},
    },
    "homepage": {
        "app_service_classes": {"homepage_service"},
        "operation_handlers": {"ensure_homepage_services_config"},
    },
}

SHARED_RUNTIME_ENTRY_MODULES = [
    ROOT / "scripts" / "bootstrap-apps.py",
    ROOT / "scripts" / "bootstrap_services" / "runtime_core.py",
    ROOT / "scripts" / "bootstrap_services" / "runtime_media_ops.py",
    ROOT / "scripts" / "bootstrap_services" / "runtime_servarr" / "service_ops.py",
    ROOT / "scripts" / "bootstrap_services" / "runtime_servarr" / "factory.py",
    ROOT / "scripts" / "bootstrap_services" / "runtime_servarr" / "prowlarr_ops.py",
    ROOT / "scripts" / "bootstrap_services" / "runtime_servarr" / "qbit_ops.py",
    ROOT / "scripts" / "bootstrap_services" / "bootstrap_runner_service.py",
    ROOT / "scripts" / "bootstrap_services" / "download_client_pipeline_service.py",
    ROOT / "scripts" / "bootstrap_services" / "runtime_factory" / "runtime_builder.py",
]


def _load_manifest(technology: str) -> dict:
    path = PLUGIN_ROOT / technology / "manifest.json"
    if not path.exists():
        raise AssertionError(f"Missing manifest for technology '{technology}': {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"Manifest must be a JSON object: {path}")
    return payload


def _iter_specs(manifest: dict) -> list[str]:
    specs = []
    for section_key in (
        "adapter_classes",
        "app_service_classes",
        "operation_handlers",
        "before_common_steps",
    ):
        section = manifest.get(section_key) or {}
        if not isinstance(section, dict):
            continue
        for value in section.values():
            token = str(value or "").strip()
            if token:
                specs.append(token)
    return specs


def _assert_import_spec_resolves(spec: str):
    if ":" not in spec:
        raise AssertionError(f"Invalid registration spec (expected module:attr): {spec}")
    module_name, attr_name = spec.split(":", 1)
    module = importlib.import_module(module_name)
    if not hasattr(module, attr_name):
        raise AssertionError(f"Registration target not found: {spec}")


class TechnologyPluggabilityContractTests(unittest.TestCase):
    def test_manifests_exist_and_match_technology_name(self):
        for tech in TECHNOLOGIES:
            manifest = _load_manifest(tech)
            self.assertEqual(
                str(manifest.get("technology") or "").strip().lower(),
                tech,
                msg=f"Manifest technology mismatch for {tech}",
            )

    def test_minimum_registration_requirements_for_each_target_technology(self):
        for tech in TECHNOLOGIES:
            manifest = _load_manifest(tech)
            expected = MIN_REGISTRATION_REQUIREMENTS.get(tech) or {}
            for section_key, required_keys in expected.items():
                section = manifest.get(section_key) or {}
                self.assertIsInstance(
                    section,
                    dict,
                    msg=f"{tech}: section '{section_key}' must be an object/map",
                )
                section_keys = set(section.keys())
                missing = set(required_keys) - section_keys
                self.assertFalse(
                    missing,
                    msg=f"{tech}: missing required {section_key} keys: {sorted(missing)}",
                )

    def test_manifest_registration_specs_are_importable(self):
        for tech in TECHNOLOGIES:
            manifest = _load_manifest(tech)
            for spec in _iter_specs(manifest):
                with self.subTest(technology=tech, spec=spec):
                    _assert_import_spec_resolves(spec)

    def test_bootstrap_entrypoint_uses_manifest_bound_handlers_for_tech_operations(self):
        entrypoint = (ROOT / "scripts" / "bootstrap-apps.py").read_text(encoding="utf-8")
        operation_names: set[str] = set()
        for tech in TECHNOLOGIES:
            manifest = _load_manifest(tech)
            handlers = manifest.get("operation_handlers") or {}
            if isinstance(handlers, dict):
                operation_names.update(str(name or "").strip() for name in handlers.keys())

        operation_names = {name for name in operation_names if name}
        self.assertTrue(operation_names, "No operation handlers discovered from manifests.")
        for op_name in sorted(operation_names):
            pattern = re.compile(
                rf"{re.escape(op_name)}\s*=\s*_missing_op_handler\(\s*\"{re.escape(op_name)}\"\s*\)",
                re.MULTILINE,
            )
            self.assertRegex(
                entrypoint,
                pattern,
                msg=(
                    f"bootstrap-apps.py must keep operation '{op_name}' manifest-driven via "
                    "_missing_op_handler(...) in wiring."
                ),
            )

    def test_shared_runtime_entry_modules_have_no_direct_app_imports(self):
        for module_path in SHARED_RUNTIME_ENTRY_MODULES:
            source = module_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(module_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = str(alias.name or "")
                        self.assertFalse(
                            name.startswith("bootstrap_services.apps."),
                            msg=(
                                f"{module_path}: direct app import '{name}' found in shared runtime "
                                "entry module. Use manifest-driven or lazy wiring."
                            ),
                        )
                elif isinstance(node, ast.ImportFrom):
                    module = str(node.module or "")
                    self.assertFalse(
                        module.startswith("bootstrap_services.apps."),
                        msg=(
                            f"{module_path}: direct app import-from '{module}' found in shared runtime "
                            "entry module. Use manifest-driven or lazy wiring."
                        ),
                    )

    def test_missing_qbittorrent_manifest_does_not_break_shared_runtime_import_init(self):
        import bootstrap_services.plugin_manifest_loader as manifest_loader
        import bootstrap_services.runtime_service_registry as registry
        import bootstrap_services.runtime_servarr.factory as runtime_factory

        prior_context = registry.get_runtime_context_cfg()
        all_manifests = manifest_loader.load_plugin_manifests()
        # Remove every manifest that provides torrent_client_service so class resolution
        # for torrent-client operations deterministically fails only when invoked.
        filtered_manifests = [
            item
            for item in all_manifests
            if "torrent_client_service" not in (item.app_service_classes or {})
        ]
        self.assertTrue(
            any(item.technology == "qbittorrent" for item in all_manifests),
            "Expected qbittorrent manifest to exist for this simulation test.",
        )
        self.assertTrue(
            len(filtered_manifests) < len(all_manifests),
            "Expected at least one manifest to provide torrent_client_service.",
        )

        try:
            with mock.patch.object(
                registry, "load_plugin_manifests", return_value=filtered_manifests
            ):
                registry.set_runtime_context_cfg({})
                # Shared runtime init/import should still work for unrelated services.
                arr_service = runtime_factory._arr_service()
                self.assertIsNotNone(arr_service)

                # The removed technology should fail only when invoked.
                with self.assertRaises(RuntimeError):
                    runtime_factory._torrent_client_service({"url": "http://qbittorrent:8080"})
        finally:
            registry.set_runtime_context_cfg(prior_context)

    def test_missing_sabnzbd_manifest_does_not_break_shared_runtime_import_init(self):
        import bootstrap_services.plugin_manifest_loader as manifest_loader
        import bootstrap_services.runtime_service_registry as registry
        import bootstrap_services.runtime_servarr.factory as runtime_factory

        prior_context = registry.get_runtime_context_cfg()
        all_manifests = manifest_loader.load_plugin_manifests()
        # Remove every manifest that provides usenet_client_service so class resolution
        # for usenet-client operations deterministically fails only when invoked.
        filtered_manifests = [
            item
            for item in all_manifests
            if "usenet_client_service" not in (item.app_service_classes or {})
        ]
        self.assertTrue(
            any(item.technology == "sabnzbd" for item in all_manifests),
            "Expected sabnzbd manifest to exist for this simulation test.",
        )
        self.assertTrue(
            len(filtered_manifests) < len(all_manifests),
            "Expected at least one manifest to provide usenet_client_service.",
        )

        try:
            with mock.patch.object(
                registry, "load_plugin_manifests", return_value=filtered_manifests
            ):
                registry.set_runtime_context_cfg({})
                # Shared runtime init/import should still work for unrelated services.
                arr_service = runtime_factory._arr_service()
                self.assertIsNotNone(arr_service)

                # The removed technology should fail only when invoked.
                with self.assertRaises(RuntimeError):
                    runtime_factory._usenet_client_service({"url": "http://sabnzbd:8080"})
        finally:
            registry.set_runtime_context_cfg(prior_context)

    def test_missing_maintainerr_manifest_does_not_break_shared_runtime_import_init(self):
        import bootstrap_services.plugin_manifest_loader as manifest_loader
        import bootstrap_services.runtime_media_ops as runtime_media_ops
        import bootstrap_services.runtime_service_registry as registry
        import bootstrap_services.runtime_servarr.factory as runtime_factory

        prior_context = registry.get_runtime_context_cfg()
        all_manifests = manifest_loader.load_plugin_manifests()
        # Remove every manifest that provides maintainerr_service so class resolution
        # for maintainerr operations deterministically fails only when invoked.
        filtered_manifests = [
            item
            for item in all_manifests
            if "maintainerr_service" not in (item.app_service_classes or {})
        ]
        self.assertTrue(
            any(item.technology == "maintainerr" for item in all_manifests),
            "Expected maintainerr manifest to exist for this simulation test.",
        )
        self.assertTrue(
            len(filtered_manifests) < len(all_manifests),
            "Expected at least one manifest to provide maintainerr_service.",
        )

        try:
            with mock.patch.object(
                registry, "load_plugin_manifests", return_value=filtered_manifests
            ):
                registry.set_runtime_context_cfg({})
                # Shared runtime init/import should still work for unrelated services.
                arr_service = runtime_factory._arr_service()
                self.assertIsNotNone(arr_service)

                # The removed technology should fail only when invoked.
                with self.assertRaises(RuntimeError):
                    runtime_media_ops._maintainerr_service({})
        finally:
            registry.set_runtime_context_cfg(prior_context)

    def test_runtime_binding_removal_is_lazy_until_technology_path_invoked(self):
        import bootstrap_services.plugin_manifest_loader as manifest_loader
        import bootstrap_services.runtime_service_registry as registry
        import bootstrap_services.runtime_servarr.factory as runtime_factory

        prior_context = registry.get_runtime_context_cfg()
        manifests = manifest_loader.load_plugin_manifests()
        runtime_hooks = manifest_loader.build_adapter_hook_defaults(manifests).to_dict()
        runtime_hooks["app_service_classes"] = dict(runtime_hooks.get("app_service_classes") or {})
        runtime_hooks["app_service_classes_by_technology"] = {
            str(tech): dict(service_map)
            for tech, service_map in (
                runtime_hooks.get("app_service_classes_by_technology") or {}
            ).items()
            if isinstance(service_map, dict)
        }
        runtime_hooks["runtime_bindings"] = {"torrent_client": "qbittorrent"}

        # Remove only the runtime-resolved torrent-client binding.
        runtime_hooks["app_service_classes"].pop("torrent_client_service", None)
        qbittorrent_map = (
            runtime_hooks["app_service_classes_by_technology"].get("qbittorrent") or {}
        )
        qbittorrent_map = dict(qbittorrent_map) if isinstance(qbittorrent_map, dict) else {}
        qbittorrent_map.pop("torrent_client_service", None)
        runtime_hooks["app_service_classes_by_technology"]["qbittorrent"] = qbittorrent_map

        try:
            registry.set_runtime_context_cfg(runtime_hooks)
            # Shared runtime init still succeeds for unrelated service wiring.
            arr_service = runtime_factory._arr_service()
            self.assertIsNotNone(arr_service)

            # Failure is deferred until we invoke the removed technology path.
            with self.assertRaises(RuntimeError):
                runtime_factory._torrent_client_service({})
        finally:
            registry.set_runtime_context_cfg(prior_context)

    def test_runtime_usenet_binding_removal_is_lazy_until_technology_path_invoked(self):
        import bootstrap_services.plugin_manifest_loader as manifest_loader
        import bootstrap_services.runtime_service_registry as registry
        import bootstrap_services.runtime_servarr.factory as runtime_factory

        prior_context = registry.get_runtime_context_cfg()
        manifests = manifest_loader.load_plugin_manifests()
        runtime_hooks = manifest_loader.build_adapter_hook_defaults(manifests).to_dict()
        runtime_hooks["app_service_classes"] = dict(runtime_hooks.get("app_service_classes") or {})
        runtime_hooks["app_service_classes_by_technology"] = {
            str(tech): dict(service_map)
            for tech, service_map in (
                runtime_hooks.get("app_service_classes_by_technology") or {}
            ).items()
            if isinstance(service_map, dict)
        }
        runtime_hooks["runtime_bindings"] = {"usenet_client": "sabnzbd"}

        # Remove only the runtime-resolved usenet-client binding.
        runtime_hooks["app_service_classes"].pop("usenet_client_service", None)
        sabnzbd_map = runtime_hooks["app_service_classes_by_technology"].get("sabnzbd") or {}
        sabnzbd_map = dict(sabnzbd_map) if isinstance(sabnzbd_map, dict) else {}
        sabnzbd_map.pop("usenet_client_service", None)
        runtime_hooks["app_service_classes_by_technology"]["sabnzbd"] = sabnzbd_map

        try:
            registry.set_runtime_context_cfg(runtime_hooks)
            # Shared runtime init still succeeds for unrelated service wiring.
            arr_service = runtime_factory._arr_service()
            self.assertIsNotNone(arr_service)

            # Failure is deferred until we invoke the removed technology path.
            with self.assertRaises(RuntimeError):
                runtime_factory._usenet_client_service({})
        finally:
            registry.set_runtime_context_cfg(prior_context)

    def test_runtime_maintainerr_binding_removal_is_lazy_until_technology_path_invoked(self):
        import bootstrap_services.plugin_manifest_loader as manifest_loader
        import bootstrap_services.runtime_media_ops as runtime_media_ops
        import bootstrap_services.runtime_service_registry as registry
        import bootstrap_services.runtime_servarr.factory as runtime_factory

        prior_context = registry.get_runtime_context_cfg()
        manifests = manifest_loader.load_plugin_manifests()
        runtime_hooks = manifest_loader.build_adapter_hook_defaults(manifests).to_dict()
        runtime_hooks["app_service_classes"] = dict(runtime_hooks.get("app_service_classes") or {})
        runtime_hooks["app_service_classes_by_technology"] = {
            str(tech): dict(service_map)
            for tech, service_map in (
                runtime_hooks.get("app_service_classes_by_technology") or {}
            ).items()
            if isinstance(service_map, dict)
        }

        # Remove only the maintainerr runtime binding.
        runtime_hooks["app_service_classes"].pop("maintainerr_service", None)
        maintainerr_map = (
            runtime_hooks["app_service_classes_by_technology"].get("maintainerr") or {}
        )
        maintainerr_map = dict(maintainerr_map) if isinstance(maintainerr_map, dict) else {}
        maintainerr_map.pop("maintainerr_service", None)
        runtime_hooks["app_service_classes_by_technology"]["maintainerr"] = maintainerr_map

        try:
            registry.set_runtime_context_cfg(runtime_hooks)
            # Shared runtime init still succeeds for unrelated service wiring.
            arr_service = runtime_factory._arr_service()
            self.assertIsNotNone(arr_service)

            # Failure is deferred until we invoke the removed technology path.
            with self.assertRaises(RuntimeError):
                runtime_media_ops._maintainerr_service({})
        finally:
            registry.set_runtime_context_cfg(prior_context)

    def test_missing_jellyfin_module_only_fails_when_jellyfin_path_is_invoked(self):
        import bootstrap_services.runtime_media_ops as runtime_media_ops

        original_import_module = importlib.import_module

        def _patched_import_module(name: str, package=None):
            if name == "bootstrap_services.apps.jellyfin.runtime_ops":
                raise ModuleNotFoundError(name)
            return original_import_module(name, package)

        # Shared runtime module is importable and basic symbols are available.
        self.assertTrue(hasattr(runtime_media_ops, "ensure_maintainerr_policy"))
        self.assertTrue(hasattr(runtime_media_ops, "_jellyfin_runtime_ops"))

        with mock.patch.object(
            runtime_media_ops.importlib, "import_module", side_effect=_patched_import_module
        ):
            # Should fail only once jellyfin-specific lazy path is explicitly invoked.
            with self.assertRaises(ModuleNotFoundError):
                runtime_media_ops._jellyfin_runtime_ops()


if __name__ == "__main__":
    unittest.main()
