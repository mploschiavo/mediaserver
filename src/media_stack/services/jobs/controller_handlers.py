"""Handler spec loading and execution for the bootstrap controller."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import argparse
import importlib
import json
import os

import media_stack.services.runtime_platform as runtime_platform
import logging


def _resolve_config_path(candidate: str | None = None) -> str | None:
    """Resolve the bootstrap config JSON path, trying multiple locations."""
    _IMAGE_CONFIG = "/opt/media-stack/contracts/media-stack.config.json"
    candidates = [
        candidate,
        os.environ.get("BOOTSTRAP_CONFIG_FILE"),
        _IMAGE_CONFIG,
    ]
    for p in candidates:
        if p and __import__("pathlib").Path(p).is_file():
            return p
    return None


def _load_handler_specs(key: str) -> list[dict]:
    """Load handler specs from per-service YAML and config.json.

    Per-service YAML plugin.preflight_handler / plugin.post_setup_handler
    are loaded first, then config.json specs are appended (deduped by name).
    """
    import json

    specs: list[dict] = []
    seen_names: set[str] = set()

    # 1. Load from per-service YAML (primary source)
    handler_field = {
        "container_preflight_handlers": "preflight_handler",
        "container_post_setup_handlers": "post_setup_handler",
    }.get(key)
    if handler_field:
        try:
            from media_stack.api.services.registry import SERVICES, _find_services_dir
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
    config_path = _resolve_config_path()
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


def _run_handler_specs(
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
    admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
    admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "media-dev")

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
            handler_fn = _resolve_handler(handler_path)
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
    with ThreadPoolExecutor(max_workers=min(6, len(parallel_specs))) as pool:
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


def _resolve_handler(spec: str):
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


def _run_preflights(state: object, args: argparse.Namespace) -> None:
    specs = _load_handler_specs("container_preflight_handlers")
    _run_handler_specs(specs, state, args, phase_label="PREFLIGHT")


def _run_post_bootstrap(state: object, args: argparse.Namespace) -> None:
    specs = _load_handler_specs("container_post_setup_handlers")
    _run_handler_specs(specs, state, args, phase_label="POST-BOOTSTRAP")


def _auto_generate_config_json(target_path: str) -> str | None:
    """Auto-generate bootstrap config JSON from service contracts + profile.

    Uses the generate_bootstrap_config module which properly handles
    the config schema, operation plans, and service defaults.
    """
    from pathlib import Path
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
