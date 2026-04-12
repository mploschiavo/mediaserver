"""Generate bootstrap config JSON from service contracts + profile.

Produces a valid bootstrap config JSON by:
1. Loading service contract defaults (jellyfin libraries, livetv, plugins, etc.)
2. Loading operation plans (adapter_hooks for phase plan execution)
3. Loading app capability defaults
4. Merging with profile settings (technology bindings, app auth)

The output matches the TopLevelBootstrapConfig schema so the bootstrap
runner can execute all configuration steps.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml


class GenerateBootstrapConfigCommand:
    """Wraps bootstrap config generation logic."""

    def generate(
        self,
        contracts_dir: Path,
        profile_path: Path | None = None,
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        """Generate bootstrap config from contracts + profile."""
        config: dict[str, Any] = {"config_version": 2}

        # 1. Load profile for technology bindings and app auth
        profile: dict = {}
        if profile_path and profile_path.is_file():
            with open(profile_path) as f:
                profile = yaml.safe_load(f) or {}
        for key in ("technology_bindings", "app_auth"):
            if key in profile:
                config[key] = profile[key]

        # 2. Load service contract defaults
        svc_dir = contracts_dir / "services"
        # Load the schema to know which service IDs are valid top-level keys
        schema_path = contracts_dir.parent / "src" / "media_stack" / "contracts" / "top_level_config_schema.json"
        allowed_keys: set[str] = set()
        if schema_path.is_file():
            try:
                schema = json.loads(schema_path.read_text())
                allowed_keys = set(schema.get("allowed_keys", schema.get("properties", {})).keys())
            except Exception:
                pass
        if svc_dir.is_dir():
            for svc_yaml in sorted(svc_dir.glob("*.yaml")):
                if svc_yaml.name.startswith("_"):
                    continue
                try:
                    with open(svc_yaml) as f:
                        svc_data = yaml.safe_load(f) or {}
                    svc_id = svc_data.get("service", {}).get("id", "")
                    defaults = svc_data.get("defaults", {})
                    if not svc_id or not defaults:
                        continue
                    if allowed_keys and svc_id not in allowed_keys:
                        continue
                    # Services with independent sub-features (e.g., jellyfin has
                    # libraries, livetv, plugins, playback as separate dicts)
                    # must be flattened to top-level keys like jellyfin_libraries.
                    # Runtime handlers read cfg.get("jellyfin_libraries"), not
                    # cfg["jellyfin"]["libraries"].
                    # Detect: if ALL values are dicts/lists (no scalars), flatten.
                    # Services with mixed types (bazarr has enabled, url as scalars)
                    # keep their single-key form: cfg.get("bazarr").
                    all_complex = defaults and all(
                        isinstance(v, (dict, list)) for v in defaults.values()
                    )
                    if all_complex:
                        for sub_key, sub_val in defaults.items():
                            config[f"{svc_id}_{sub_key}"] = sub_val
                    else:
                        config[svc_id] = defaults
                except Exception:
                    pass

        # 3. Load operation plans AND event handlers as adapter_hooks
        adapter_hooks: dict[str, Any] = {}
        src_contracts = contracts_dir.parent / "src" / "media_stack" / "contracts"
        # Operation plan files
        for plan_file in sorted(src_contracts.glob("*_operation_plans.json")) if src_contracts.is_dir() else []:
            try:
                plan_data = json.loads(plan_file.read_text())
                if isinstance(plan_data, dict):
                    adapter_hooks.update(plan_data)
            except Exception:
                pass
        # Event handlers + runner_phase_scripts from service contracts
        runner_phase_scripts: dict[str, str] = {}
        if svc_dir.is_dir():
            for svc_yaml in sorted(svc_dir.glob("*.yaml")):
                if svc_yaml.name.startswith("_"):
                    continue
                try:
                    with open(svc_yaml) as f:
                        svc_data = yaml.safe_load(f) or {}
                    plugin = svc_data.get("plugin", {})
                    if not isinstance(plugin, dict):
                        continue
                    # Event handlers (ENSURE, RUN, POST, etc.)
                    event_handlers = plugin.get("event_handlers", {})
                    if isinstance(event_handlers, dict):
                        for phase, handlers in event_handlers.items():
                            if isinstance(handlers, dict):
                                adapter_hooks.setdefault("event_handlers", {}).setdefault(phase, {}).update(handlers)
                    # Runner phase scripts
                    phase_scripts = plugin.get("phase_scripts", {})
                    if isinstance(phase_scripts, dict):
                        runner_phase_scripts.update(phase_scripts)
                except Exception:
                    pass
        if runner_phase_scripts:
            adapter_hooks["runner_phase_scripts"] = runner_phase_scripts
        if adapter_hooks:
            config["adapter_hooks"] = adapter_hooks

        # 4. Load app capability defaults
        defaults_json = src_contracts / "app_capability_defaults.json"
        if defaults_json.is_file():
            try:
                config["app_capability_defaults"] = json.loads(defaults_json.read_text())
            except Exception:
                pass

        # 5. Write output
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(config, indent=2, default=str))

        return config

    def main(self) -> None:
        root = Path(__file__).resolve().parents[4]
        contracts = root / "contracts"
        profile = contracts / "media-stack.profile.yaml"
        output = contracts / "media-stack.config.json"

        if len(sys.argv) > 1:
            contracts = Path(sys.argv[1])
        if len(sys.argv) > 2:
            output = Path(sys.argv[2])
        if len(sys.argv) > 3:
            profile = Path(sys.argv[3])

        config = self.generate(contracts, profile, output)
        print(f"Generated {output} ({len(config)} top-level keys)")


_instance = GenerateBootstrapConfigCommand()
generate = _instance.generate
main = _instance.main


if __name__ == "__main__":
    main()
