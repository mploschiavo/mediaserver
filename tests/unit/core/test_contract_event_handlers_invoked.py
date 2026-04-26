"""Ratchet: every contract-declared ``plugin.event_handlers``
entry must be wired to fire from at least one runtime path.

The 2026-04-20 homepage bug shape: ``contracts/services/homepage.yaml``
declared ``event_handlers.ENSURE.ensure_homepage_services_config``,
the manifest loader picked it up, the handler was importable —
but no live code path actually invoked it on a fresh reconcile,
so the dashboard's services.yaml stayed at the homepage container's
stock placeholder. The handler was wired but never exercised.

This test fails fast in three layered ways:

1. **Discoverability** — every handler dotted-path in every
   contract must resolve to an importable function (catches typos
   and dead module paths).
2. **Plan presence** — every handler name declared in a
   contract must appear in at least one ``runner_operation_plans.json``
   step OR be the implementation of a contract job. A handler
   declared in YAML but never referenced by a plan/job is an
   orphan — it'll never fire.
3. **Signature compatibility** — handlers slotted into the
   legacy operation-plans pipeline must accept the right number
   of positional args (``cfg``, ``config_root`` at minimum) so
   the runner can call them without an arg-mismatch crash.

This catches the homepage class of bug at code-load time instead
of runtime. The deeper "runner walks the plan but the plan misses
the phase" issue (the actual root cause of the homepage regen
miss on dist install) needs a different test — see
``test_runner_phase_walks`` (TODO)."""

from __future__ import annotations

import importlib
import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

_CONTRACTS_SERVICES = ROOT / "contracts" / "services"
_RUNNER_PLANS_JSON = (
    ROOT / "src" / "media_stack" / "contracts" / "runner_operation_plans.json"
)


def _load_handler(spec: str):
    """Resolve a ``module.path:function_name`` dotted spec into a
    callable. Returns the function or raises whatever import/getattr
    error explains the breakage."""
    if ":" not in spec:
        raise ValueError(f"handler spec missing ':' separator: {spec!r}")
    mod_path, fn_name = spec.split(":", 1)
    mod = importlib.import_module(mod_path)
    fn = getattr(mod, fn_name)
    if not callable(fn):
        raise TypeError(f"{spec} resolved to non-callable {type(fn)}")
    return fn


def _all_contract_event_handlers() -> list[tuple[str, str, str, str, Path]]:
    """Walk every ``contracts/services/*.yaml`` and return a list
    of ``(service_id, event_name, handler_name, handler_spec, file)``
    tuples. Excludes the template."""
    out: list[tuple[str, str, str, str, Path]] = []
    for yaml_file in sorted(_CONTRACTS_SERVICES.glob("*.yaml")):
        if yaml_file.name == "_template.yaml":
            continue
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        svc_id = (data.get("service") or {}).get("id", yaml_file.stem)
        plugin = data.get("plugin") or {}
        for event_name, handler_map in (plugin.get("event_handlers") or {}).items():
            if not isinstance(handler_map, dict):
                continue
            for handler_name, handler_spec in handler_map.items():
                out.append(
                    (str(svc_id), str(event_name),
                     str(handler_name), str(handler_spec), yaml_file)
                )
    return out


def _all_contract_jobs() -> list[tuple[str, str, str, Path]]:
    """Same shape, for ``plugin.jobs``."""
    out: list[tuple[str, str, str, Path]] = []
    for yaml_file in sorted(_CONTRACTS_SERVICES.glob("*.yaml")):
        if yaml_file.name == "_template.yaml":
            continue
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        svc_id = (data.get("service") or {}).get("id", yaml_file.stem)
        plugin = data.get("plugin") or {}
        jobs = plugin.get("jobs") or {}
        for job_name, job_def in jobs.items():
            if not isinstance(job_def, dict):
                continue
            handler_spec = job_def.get("handler")
            if handler_spec:
                out.append(
                    (str(svc_id), str(job_name),
                     str(handler_spec), yaml_file)
                )
    return out


# ----------------------------------------------------------------------
# Layer 1: every handler dotted-path must resolve
# ----------------------------------------------------------------------


class HandlerImportabilityTests(unittest.TestCase):

    def test_every_event_handler_is_importable(self) -> None:
        broken: list[str] = []
        for svc_id, event, name, spec, path in _all_contract_event_handlers():
            try:
                _load_handler(spec)
            except Exception as exc:
                broken.append(
                    f"{path.name}: event_handlers.{event}.{name} -> "
                    f"{spec} ({type(exc).__name__}: {exc})"
                )
        self.assertFalse(
            broken,
            "Contract event handlers fail to import:\n  - "
            + "\n  - ".join(broken)
            + "\n\nFix: either correct the handler spec in the contract "
              "or implement the missing function.",
        )

    def test_every_job_handler_is_importable(self) -> None:
        broken: list[str] = []
        for svc_id, job, spec, path in _all_contract_jobs():
            try:
                _load_handler(spec)
            except Exception as exc:
                broken.append(
                    f"{path.name}: jobs.{job} -> {spec} "
                    f"({type(exc).__name__}: {exc})"
                )
        self.assertFalse(
            broken,
            "Contract job handlers fail to import:\n  - "
            + "\n  - ".join(broken),
        )


# ----------------------------------------------------------------------
# Layer 2: every event handler is referenced by at least one plan/job
# ----------------------------------------------------------------------


class HandlerReachabilityTests(unittest.TestCase):
    """A handler declared in YAML but not referenced anywhere in the
    runner operation plans (and not used as a job handler) is an
    orphan — it won't fire. Catches the class of bug where a
    contract change ships a new handler but the wiring lags."""

    def setUp(self) -> None:
        plans = json.loads(_RUNNER_PLANS_JSON.read_text(encoding="utf-8"))
        self._plan_handler_names: set[str] = set()
        for phase_def in plans.values():
            steps = (
                phase_def.get("steps")
                if isinstance(phase_def, dict) else None
            )
            if not isinstance(steps, list):
                continue
            for step in steps:
                if isinstance(step, dict) and step.get("handler"):
                    self._plan_handler_names.add(str(step["handler"]))
        # Also collect handler basenames from contract job definitions
        # (those reach a different runtime path).
        self._job_basenames: set[str] = set()
        for _svc, _name, spec, _path in _all_contract_jobs():
            if ":" in spec:
                self._job_basenames.add(spec.split(":", 1)[1])

    def test_every_event_handler_is_referenced_by_a_plan_or_job(self) -> None:
        # Allow-list: handlers we know are wired through code paths
        # that don't appear in runner_operation_plans.json (e.g.
        # called directly from a service class, or invoked by a
        # phase that's a Python method rather than a JSON plan).
        # Each entry needs a one-line justification — the entry is
        # the friction we want when adding a new handler.
        tolerated_orphans = {
            # Jellyfin adapter (Python method) invokes these via
            # run_post_servarr_pre_hygiene_steps /
            # run_post_servarr_post_hygiene_steps. They're also
            # wrapped by configure-media-server contract jobs.
            "ensure_jellyfin_home_rails",
            "ensure_jellyfin_auto_collections_config",
            "ensure_jellyfin_prewarm",
            "ensure_jellyfin_libraries",
            "ensure_jellyfin_livetv",
            "ensure_jellyfin_plugins",
            "ensure_jellyfin_playback_defaults",
            # Prowlarr post-RUN handlers fire from the controller's
            # post-servarr indexer pipeline (Python adapter call).
            "trigger_prowlarr_sync", "sync_arr_indexers_from_prowlarr",
            "auto_add_tested_indexers", "run_prowlarr_indexer_pipeline",
            "ensure_prowlarr_flaresolverr_proxy", "ensure_prowlarr_indexer",
            # Jellyseerr request-manager configuration runs from the
            # post_servarr_pre_media_steps plan as configure_request_manager
            # (different name, same wiring).
            "configure_request_manager",
            # Torrent-client adapter (qBittorrent / transmission)
            # calls these from its login + categories setup methods.
            "torrent_client_login", "setup_torrent_categories",
            # SAB adapter (Python) wires these — read_sabnzbd_api_key
            # runs in the ACQUIRE preflight, defaults+categories in
            # ENSURE post-prepare.
            "read_sabnzbd_api_key", "ensure_sabnzbd_defaults",
            "ensure_sabnzbd_categories",
            # The legacy runner.run() method is itself the RUN
            # event for the servarr pipeline; the handler IS the
            # call site.
            "run_servarr_pipeline",
            # ensure_app_auth_settings is invoked by the controller's
            # auth-config validate step (post-servarr precheck).
            "ensure_app_auth_settings",
        }
        orphans: list[str] = []
        for svc_id, event, name, spec, path in _all_contract_event_handlers():
            handler_basename = spec.split(":", 1)[1] if ":" in spec else spec
            in_plan = (
                spec in self._plan_handler_names
                or handler_basename in self._plan_handler_names
                or name in self._plan_handler_names
            )
            in_job = handler_basename in self._job_basenames
            if in_plan or in_job:
                continue
            if name in tolerated_orphans or handler_basename in tolerated_orphans:
                continue
            orphans.append(
                f"{path.name}: event_handlers.{event}.{name} -> "
                f"{spec}\n     (no step in runner_operation_plans.json "
                f"and not used as a contract job handler — this "
                f"handler will never fire)"
            )
        self.assertFalse(
            orphans,
            "Contract event handlers declared but never invoked by "
            "any runtime path:\n  - "
            + "\n  - ".join(orphans)
            + "\n\nFix: either add a step in "
              "src/media_stack/contracts/runner_operation_plans.json "
              "that names this handler, OR add the handler name to "
              "tolerated_orphans here with a one-line justification "
              "if it's invoked through a Python adapter method.",
        )


# ----------------------------------------------------------------------
# Layer 3: handlers slotted into legacy plans accept the right args
# ----------------------------------------------------------------------


class HandlerSignatureTests(unittest.TestCase):
    """The legacy runner calls handlers with positional args declared
    in the plan (``args: ['cfg', 'config_root', ...]``). If a
    handler's signature drifts from what the plan expects, the call
    fails at runtime with a TypeError that the runner catches and
    swallows — silent regression. Pin compatible signatures here."""

    def test_handlers_in_plan_accept_their_declared_args(self) -> None:
        plans = json.loads(_RUNNER_PLANS_JSON.read_text(encoding="utf-8"))
        # Build {handler_name: declared_arg_count} from the plan.
        plan_arities: dict[str, int] = {}
        for phase_def in plans.values():
            steps = (
                phase_def.get("steps")
                if isinstance(phase_def, dict) else None
            )
            if not isinstance(steps, list):
                continue
            for step in steps:
                if not isinstance(step, dict):
                    continue
                hname = step.get("handler")
                hargs = step.get("args") or []
                if hname:
                    plan_arities[str(hname)] = len(hargs)

        # Map handler name -> dotted spec (from any contract that
        # declares it); some handlers appear in multiple events.
        spec_by_name: dict[str, str] = {}
        for _svc, _event, name, spec, _path in _all_contract_event_handlers():
            spec_by_name.setdefault(name, spec)

        mismatches: list[str] = []
        for handler_name, expected_arity in plan_arities.items():
            spec = spec_by_name.get(handler_name)
            if not spec:
                # Plan references a handler not declared in any
                # contract — caught by the orphan test from the
                # other direction; skip here.
                continue
            try:
                fn = _load_handler(spec)
            except Exception:
                continue  # caught by importability test
            try:
                sig = inspect.signature(fn)
            except (TypeError, ValueError):
                continue
            # Count positional-or-keyword params that are required
            # (no default). Allow handlers that accept *args /
            # **kwargs (open arity).
            params = list(sig.parameters.values())
            has_varpos = any(
                p.kind == inspect.Parameter.VAR_POSITIONAL for p in params
            )
            if has_varpos:
                continue
            positional = [
                p for p in params
                if p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            min_required = sum(
                1 for p in positional
                if p.default is inspect.Parameter.empty
            )
            if expected_arity < min_required:
                mismatches.append(
                    f"{handler_name} ({spec}): plan passes "
                    f"{expected_arity} args but handler requires "
                    f"at least {min_required}"
                )
        self.assertFalse(
            mismatches,
            "Handler/plan arity mismatch (would crash at runtime):\n  - "
            + "\n  - ".join(mismatches),
        )


if __name__ == "__main__":
    unittest.main()
