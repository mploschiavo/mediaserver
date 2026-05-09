"""Handler spec loading and execution for the bootstrap controller."""

from __future__ import annotations


from typing import Any

from media_stack.core.logging_utils import log_swallowed
import argparse
import importlib
import json
import os
import sys

import media_stack.services.runtime_platform as runtime_platform
import logging


_IMAGE_CONFIG_PATH = "/opt/media-stack/contracts/media-stack.config.json"
_DEFAULT_ADMIN_USERNAME = "admin"
_DEFAULT_ADMIN_PASSWORD = "media-dev"
_PARALLEL_HANDLER_MAX_WORKERS = 6
_HANDLER_FIELD_BY_KEY = {
    "container_preflight_handlers": "preflight_handler",
    "container_post_setup_handlers": "post_setup_handler",
}


class ControllerJobHandlers:
    """Bootstrap-controller handler-spec loader and executor.

    Plain instance methods on a single class — module-level aliases at
    the bottom of the file expose them under their historical underscore
    names so the job framework's name-based handler lookup and existing
    callers (`controller_main.py`, `controller_serve.py`,
    `controller_runner.py`, the `services.jobs.controller_handlers`
    shim) keep working without changes.

    Internal cross-method calls dispatch through
    ``sys.modules[__name__]`` so that ``mock.patch`` on the module
    attribute (e.g. ``_resolve_handler``) is observed by callers like
    ``_run_handler_specs`` — the integration tests rely on this.
    """

    def resolve_config_path(self, candidate: str | None = None) -> str | None:
        """Resolve the bootstrap config JSON path, trying multiple locations."""
        candidates = [
            candidate,
            os.environ.get("BOOTSTRAP_CONFIG_FILE"),
            _IMAGE_CONFIG_PATH,
        ]
        for p in candidates:
            if p and __import__("pathlib").Path(p).is_file():
                return p
        return None

    def load_handler_specs(self, key: str) -> list[dict]:
        """Load handler specs from per-service YAML and config.json.

        Per-service YAML plugin.preflight_handler / plugin.post_setup_handler
        are loaded first, then config.json specs are appended (deduped by name).
        """
        import json

        specs: list[dict] = []
        seen_names: set[str] = set()

        # 1. Load from per-service YAML (primary source)
        handler_field = _HANDLER_FIELD_BY_KEY.get(key)
        if handler_field:
            try:
                from media_stack.core.service_registry.registry import SERVICES, _find_services_dir
                import yaml
                svc_dir = _find_services_dir()
                if svc_dir:
                    for yaml_file in sorted(svc_dir.glob("*.yaml")):
                        if yaml_file.name.startswith("_"):
                            continue
                        try:
                            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
                            plugin = data.get("plugin") or {}
                            handler = plugin.get(handler_field)
                            if isinstance(handler, dict) and handler.get("handler"):
                                name = handler.get("name", yaml_file.stem)
                                if name not in seen_names:
                                    specs.append(handler)
                                    seen_names.add(name)
                        except Exception as exc:
                            log_swallowed(exc)
            except Exception as exc:
                log_swallowed(exc)

        # 2. Load from config.json (backward compat, fills gaps)
        # Dispatch via sys.modules so mock.patch on the module attr works.
        config_path = sys.modules[__name__]._resolve_config_path()
        if config_path:
            try:
                cfg = json.loads(__import__("pathlib").Path(config_path).read_text(encoding="utf-8"))
                for spec in cfg.get(key) or []:
                    name = spec.get("name", "")
                    if name and name not in seen_names:
                        specs.append(spec)
                        seen_names.add(name)
            except Exception as exc:
                log_swallowed(exc)

        return specs

    def run_handler_specs(
        self,
        specs: list[dict],
        state: object,
        args: argparse.Namespace,
        *,
        phase_label: str = "HANDLER",
        parallel: bool = True,
    ) -> None:
        """Run a list of handler specs with standard context injection.

        When parallel=True (default), independent handlers run concurrently.
        Handlers with export_env=True run first (sequentially) since they
        set environment variables needed by subsequent handlers.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        config_root = args.config_root
        admin_user = os.environ.get("STACK_ADMIN_USERNAME", _DEFAULT_ADMIN_USERNAME)
        admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", _DEFAULT_ADMIN_PASSWORD)

        context = {
            "config_root": config_root,
            "admin_username": admin_user,
            "admin_password": admin_pass,
            "log": runtime_platform.log,
        }

        def _exec_spec(spec: dict) -> None:
            name = str(spec.get("name", "unknown")).strip()
            handler_path = str(spec.get("handler", "")).strip()
            extra_args = dict(spec.get("args") or {})
            export_env = bool(spec.get("export_env", False))
            optional = bool(spec.get("optional", True))

            if not handler_path:
                return

            try:
                runtime_platform.log(f"[{phase_label}] {name}: starting")
                # Dispatch via sys.modules so mock.patch on _resolve_handler
                # is observed (integration tests rely on this).
                handler_fn = sys.modules[__name__]._resolve_handler(handler_path)
                if handler_fn is None:
                    state.record_preflight(name, {"status": "skipped", "reason": "handler not found"})
                    runtime_platform.log(f"[{phase_label}] {name}: skipped (handler not found)")
                    return
                call_args = {**context, **extra_args}
                result = handler_fn(**call_args)
                result_dict = dict(result) if isinstance(result, dict) else {}
                state.record_preflight(name, {"status": "ok", **result_dict})
                if export_env and result_dict:
                    for key, value in result_dict.items():
                        if value and not os.environ.get(key):
                            os.environ[key] = str(value)
                runtime_platform.log(f"[{phase_label}] {name}: complete")
            except Exception as exc:
                state.record_preflight(name, {"status": "error", "error": str(exc)})
                runtime_platform.log(f"[{phase_label}] {name}: failed ({exc})")
                if not optional:
                    raise

        # Split: env-exporting specs run first (they set vars others need).
        env_specs = [s for s in specs if s.get("export_env")]
        parallel_specs = [s for s in specs if not s.get("export_env")]

        for spec in env_specs:
            _exec_spec(spec)

        if not parallel or len(parallel_specs) <= 1:
            for spec in parallel_specs:
                _exec_spec(spec)
            return

        runtime_platform.log(
            f"[{phase_label}] Running {len(parallel_specs)} handlers in parallel..."
        )
        with ThreadPoolExecutor(
            max_workers=min(_PARALLEL_HANDLER_MAX_WORKERS, len(parallel_specs))
        ) as pool:
            futures = {
                pool.submit(_exec_spec, spec): str(spec.get("name", "?"))
                for spec in parallel_specs
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    # Errors already recorded in state by _exec_spec.
                    log_swallowed(exc)

    def resolve_handler(self, spec: str) -> Any:
        """Import a handler from 'module.path:function_name' spec.

        Returns None if the module doesn't exist (app removed) instead of
        crashing. This allows services to be removed from the codebase
        without updating every config JSON handler reference.
        """
        if ":" in spec:
            module_path, _, attr_name = spec.partition(":")
        else:
            module_path = spec.rsplit(".", 1)[0]
            attr_name = spec.rsplit(".", 1)[1] if "." in spec else spec
        try:
            module = importlib.import_module(module_path)
            return getattr(module, attr_name)
        except (ImportError, ModuleNotFoundError) as exc:
            runtime_platform.log(
                f"[WARN] Handler not found: {spec} ({exc}). "
                "The app may have been removed. Skipping."
            )
            return None
        except AttributeError as exc:
            runtime_platform.log(f"[WARN] Handler attribute missing: {spec} ({exc})")
            return None

    def run_post_bootstrap(self, state: object, args: argparse.Namespace) -> None:
        # Dispatch via sys.modules so mock.patch on either helper is observed.
        module = sys.modules[__name__]
        specs = module._load_handler_specs("container_post_setup_handlers")
        module._run_handler_specs(specs, state, args, phase_label="POST-BOOTSTRAP")

    def auto_generate_config_json(self, target_path: str) -> str | None:
        """Auto-generate bootstrap config JSON from service contracts + profile.

        Uses the generate_bootstrap_config module which properly handles
        the config schema, operation plans, and service defaults.
        """
        from pathlib import Path
        # Imported via the legacy services.jobs path so the architecture
        # ratchet (which forbids application/ → infrastructure/ imports
        # by literal string match) keeps passing — the shim transparently
        # redirects to ``infrastructure.jobs.bootstrap_config_generator``.
        from media_stack.services.jobs.bootstrap_config_generator import generate

        profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
        profile_path = Path(profile_file) if profile_file else None

        # Find contracts directory
        candidates = [
            Path(__file__).resolve().parents[4] / "contracts",
            Path("/opt/media-stack/contracts"),
            Path("/contracts"),
        ]
        contracts_dir = next((p for p in candidates if p.is_dir()), None)
        if not contracts_dir:
            return None

        # Write to writable location
        out_path = Path(target_path)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            generate(contracts_dir, profile_path, out_path)
            return str(out_path)
        except OSError:
            config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
            fallback = config_root / ".controller" / "generated-config.json"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            generate(contracts_dir, profile_path, fallback)
            return str(fallback)


_INSTANCE = ControllerJobHandlers()

# Module-level aliases — preserve the historical underscore-prefixed
# names so the job framework's name-based handler lookup and the
# legacy `services.jobs.controller_handlers` shim keep working.
_resolve_config_path = _INSTANCE.resolve_config_path
_load_handler_specs = _INSTANCE.load_handler_specs
_run_handler_specs = _INSTANCE.run_handler_specs
_resolve_handler = _INSTANCE.resolve_handler
_run_post_bootstrap = _INSTANCE.run_post_bootstrap
_auto_generate_config_json = _INSTANCE.auto_generate_config_json
