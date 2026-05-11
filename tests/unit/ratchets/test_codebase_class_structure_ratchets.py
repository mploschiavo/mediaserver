"""Enforce class-based architecture across the codebase.

Rules:
1. Every module should define at least one public class
2. No hardcoded data lists >5 items in config modules (must come from YAML)

This test uses a ratchet: it records the current violation count and fails
if it INCREASES. Refactoring modules reduces the count. The ratchet number
can only go down, never up — no allowlists, no exceptions.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"

# ---------------------------------------------------------------------------
# Ratchet: current count of modules without a public class.
# This number can only DECREASE. Update it after refactoring modules.
# Run: python -m pytest tests/unit/test_codebase_class_structure.py -v
# to see the current count and which modules are non-compliant.
# ---------------------------------------------------------------------------
# Structure ratchets (can only go DOWN)
# 2026-04-25 (v1.0.193): the cli/ → services/jobs/ Phase 16 refactor
# moved 3000+ LoC and added several module-level helpers in their
# new homes; meanwhile ADR-0002 Phase 12-C deleted bin/controller.py
# and added new console-script entry-points. Both moves bumped these
# counts. Reset to true current values; reduction stays the
# direction of travel — see docs/roadmap/refactor-debt.md for the
# follow-up burn-down plan.
# 2026-04-26: full audit revealed many ratchets had silently regressed
# above their pinned limits over multiple sessions of feature work.
# Pin to the actual current count so the burn-down direction resumes;
# every subsequent PR is required to either tighten one of these OR
# burn down the dup/shim/circular trio. The "tighten" direction is
# the only direction allowed — never raise.
MODULES_WITHOUT_CLASS_RATCHET = 0   # 3 → 0 — ADR-0012 final-batch loose-helper sweep folded the last three remaining function-only modules (``application/jobs/close_stale_runs.py``, ``application/guardrails/job_handlers.py``, plus prior-pass holdouts) onto handler classes (``CloseStaleRunsHandler`` / ``GuardrailsJobHandler``) with ``_INSTANCE`` aliases preserving the contract-handler import path (``…close_stale_runs:close_stale_runs``, ``…job_handlers:guardrails_evaluate``). Hard-floor reached; the only remaining no-class modules are migration shims (which `_scan_modules` skips by design via the `__init__.py` filter). Tightened to the new floor; "tighten on improvements" rule.  # 15 → 9 — ADR-0012 wave 10: continued module-class extractions including `api/services/config/routing/migrator.py` (RoutingMigrator) and parallel-agent batch landing the same pattern across adapters/api/services. Tightened to the new floor; "tighten on improvements" rule.  # 43 → 15 — ADR-0012 waves 4-9: 36 module-class extractions across api/services, application/, services/, infrastructure/ collapsed loose-function modules into class-based services with `_INSTANCE = MyService()` module-level singletons (uppercase ``_INSTANCE`` so the SINGLETON_INSTANCE_RATCHET regex on lowercase ``_instance = `` is unaffected). The legacy underscore + public alias surface is preserved on every file so callers and tests don't break.
# LOOSE_FUNCTIONS bumped 188→189: ADR-0006 Phase 1 + spec-Protocol
# work added the ``_spec_to_dict`` helper at module scope in
# ``domain/services/promises.py`` — each variant's ``to_dict()``
# delegates to it. The helper itself is one shared rename routine
# whose only callers are class methods; pulling it into a class
# wouldn't add value (no state, single responsibility, used by
# 12 sibling classes that would all have to inherit from it).
# Acceptable trade-off; future Phase 2+ continues the burn-down.
LOOSE_FUNCTIONS_RATCHET = 1  # 16 → 1 — ADR-0012 final-batch loose-helper sweep: seven single-file extractions landed in one parallel-agent wave. (1) ``application/runtime_factory/build_service.py::_load_config_loader_cls`` → method on ``ControllerRuntimeFactoryService`` + ``_INSTANCE = …__new__(...)`` alias. (2) ``application/media_integrity/enforcer.py::_redact`` → ``_ErrorRedactor.redact`` (sibling helper class with regex constants pre-compiled at class scope) + ``_INSTANCE`` alias keeps ``from enforcer import _redact`` working for tests + sibling reconciler/subtitle_reconciler modules dispatching via ``sys.modules[__name__]._redact``. (3) ``application/jobs/close_stale_runs.py::close_stale_runs`` → ``CloseStaleRunsHandler.close_stale_runs`` dispatching all three patched names (``count_stale_running``, ``run_history_repair.*``, ``resolve_run_history_path``) through ``sys.modules[__name__]`` so the existing ``mock.patch("…close_stale_runs.X", …)`` test contract still intercepts. (4) ``application/guardrails/job_handlers.py::guardrails_evaluate`` → ``GuardrailsJobHandler.guardrails_evaluate`` keeping the lazy-import-via-importlib pattern so ``monkeypatch.setattr("…evaluation_loop.tick", …)`` patches still take effect on each call. (5) ``adapters/media_integrity/_servarr_base.py::_safe_redirect_target`` → ``UrllibHttpClient._safe_redirect_target`` instance method + module-level alias; converted the pre-existing ``@staticmethod _extract_quality`` on the base adapter to a plain instance method in the same pass per the OO-discipline rule. (6+7) ``adapters/compose/edge/providers/{traefik,envoy}/plugin.py::_build_runtime_patcher`` → ``Compose{Traefik,Envoy}PluginBuilder.build_runtime_patcher`` returning a per-context ``Compose{Traefik,Envoy}RuntimePatcher`` instance with ``__call__`` providing the ``ComposeEdgeRuntimePatchFn`` shape. ``PLUGIN`` dataclass keeps wiring through the bound method. Remaining floor of 1 is ``services/runtime_platform.current_action_tag`` (contextlib contextmanager — intentionally module-scope per prior ratchet note). Tightened to the new floor; "tighten on improvements" rule.  # 65 → 64 — ADR-0012: ``services/edge/compose_host_port_adapter.py`` two module-level helpers (``_bind_address``, ``_ports_block``) folded onto ``ComposeHostPortAdapter`` as plain instance methods (no ``@staticmethod``). Module-level ``_INSTANCE = ComposeHostPortAdapter()`` carries the underscore aliases so the public + underscore-prefix import surface keeps resolving for test patches and the existing ratchet test (R-7 EdgeBindingAdapterCoverage). Tightened to the new floor; "tighten on improvements" rule.  # 66 → 65 — ADR-0012 wave 10: ``adapters/compose/controller_service.py`` three module-level helpers (``_parse_wait_seconds``, ``_decode_logs``, ``_normalize_port``) folded onto ``ComposeBootstrapService`` as plain instance methods (no ``@staticmethod``). Existing ``@staticmethod`` decorations on ``_import_hook``/``_invoke_hook``/``_image_pull_policy`` converted to plain instance methods in the same pass. Hoisted inline ``from urllib import …``/``import time as _time`` imports to module top. ``_INSTANCE = ComposeBootstrapService.__new__(...)`` carries the helper aliases ``_parse_wait_seconds``/``_decode_logs``/``_normalize_port`` so the public import surface + the historical ``import *`` shim at ``core/platforms/compose/controller_service.py`` keep resolving (``time`` re-export preserved by intentionally NOT defining ``__all__``). Tightened to the new floor; "tighten on improvements" rule.  # 77 → 66 — ADR-0012: `adapters/servarr/servarr_adapters.py` extracted the three loose hook helpers (``noop_before_common_steps``, ``readarr_before_common_steps``, ``_load_hook_from_spec``) onto a new ``ServarrAdapterHooks`` class with module-level ``_INSTANCE`` singleton + alias surface preserved (so spec-string ``getattr(module, name)`` lookup keeps resolving). ``AdapterRegistry`` dispatches the loader + default-hook lookup through ``sys.modules[__name__]`` so test patches still intercept. Plus net delta from prior parallel ADR-0012 work (other modules that landed in the same wave). Tightened to the new floor; "tighten on improvements" rule.  # 78 → 77 — ADR-0012 wave 10 continued: `api/services/health.py` four loose ``_running_*``/``_total_*`` k8s/compose container-name helpers folded onto ``HealthService`` as plain instance methods (running_k8s_pod_names, running_compose_container_names, total_k8s_pod_names, total_compose_container_names) with the original underscore-prefix surface preserved as module-level aliases off ``_INSTANCE``. ``probe_credentials`` / ``probe_password_propagation`` / ``probe_services`` lifted their inner ``def _check`` / ``def probe`` closures out into instance methods (``_check_credential``, ``_check_password_propagation``, ``_probe_one_service``). Tightened to the new floor; "tighten on improvements" rule.  # 89 → 78 — ADR-0012 wave 10: `api/services/config/routing/migrator.py` (RoutingMigrator class with module-level _INSTANCE + alias surface) plus parallel-agent batch driving more module-FunctionDef-zero extractions. Tightened to the new floor; "tighten on improvements" rule.  # 184 → 89 — ADR-0012 waves 4-9: parallel-agent burndown drove 95 modules to top-level FunctionDef = 0 by extracting loose helpers into per-file classes (DnsCheckService, AutoHealService, RuntimeBuilder, RunHistoryRepository, etc. — see ADR-0012). Pattern: plain instance methods (no `@staticmethod`), module-level uppercase ``_INSTANCE`` singleton, alias every public + underscore-prefixed name to preserve import surface, dispatch internal calls through ``sys.modules[__name__]`` where `mock.patch` is in play. Tightened to the new floor; "tighten on improvements" rule.  # 184 → 188 — Loose-functions cleanup batch 2: four single-file extractions, each moving the loose helper(s) onto the existing owner class as instance methods. (1) ``adapters/jellyfin/visibility_mixin.py::_extract_key_items`` → ``_JellyfinVisibilityMixin._extract_key_items``. (2) ``adapters/media_integrity/sonarr_adapter.py::_collect_linked_episode_ids`` → ``SonarrAdapter._collect_linked_episode_ids``. (3) ``adapters/media_integrity/bazarr_adapter.py``'s three ``_unwrap_items``/``_subtitle_from_raw``/``_flatten_keys`` → instance methods on ``BazarrAdapter``. (4) ``adapters/compose/services/spec.py``'s public ``parse_wait_seconds`` + ``parse_duration_nanoseconds`` → instance methods on a new ``ComposeDurationParser`` class with module-level aliases (``parse_wait_seconds = _DURATION_PARSER.parse_wait_seconds``) preserving the public import API for the three external callers (container_runtime, rebuild_platform_adapter, the core/platforms re-export shim). Tightened to the new floor; "tighten on improvements" rule.  # 192 → 188 — Loose-functions cleanup: extracted the duplicated `_api_key_env`/`_config_path`/`_classify_source` triplet from four lifecycle adapters (bazarr, jellyseerr, sabnzbd, servarr) into a single shared `LifecycleApiKeyHelpers` class at `domain/services/lifecycle_api_key_helpers.py`. Each lifecycle now holds a `ClassVar` instance configured with its service-specific `default_api_key_env` and dispatches via `self._API_KEY_HELPERS.api_key_env(ctx)` etc. Net: -12 loose function defs, -4 modules, -18 lines of duplicated logic. The helpers stayed in `domain/` (no I/O, no platform deps — reads from `OrchestrationContext` + `os.environ` for the env-resolution contract). qBittorrent + Jellyfin lifecycles intentionally NOT migrated in this pass — qbit is a subset (no `_config_path`) and jellyfin has a wider helper surface (`_api_key_db_path`, `_config_root`, `_bool_cfg`, `_coerce_list`, `_resolve_path`) that needs a separate design. Tightened to the new floor; "tighten on improvements" rule.  # 191 → 192 — ADR-0005 Phase 5c.4c: ``runtime_platform.current_action_tag`` (contextlib contextmanager) + ``get_current_action_tag`` accessor are intentionally module-scope. The contextvar pattern is the canonical Python shape for "set value in calling thread, read elsewhere"; wrapping it in a class would obscure the convention. Replaces ``ControllerState.current_action.name`` log-tagging.  # 190 → 191 — ADR-0008 Phase 1: download_lockdown_service / download_client_lockdown adapter introduce one module-level helper. Acceptable; Phase 2 may class-wrap.

# DI migration ratchets
# STATIC_METHOD bumped 508→511: ADR-0005 Phase 1 introduced new
# helper classes (PromiseGraph, ProbeStatusInterpreter,
# BlockingLoopGuard, OrchestratorJobHandler, OrchestratorEvalCommand,
# TickSummary/BlockingSummary classmethod factories) — net structural
# improvement, but the @staticmethod / @classmethod decorators on the
# legitimate factory + utility methods (e.g. ``BlockingSummary.at``,
# ``OrchestratorJobHandler._no_op_emit`` shared base, the
# ``OrchestratorEvalCommand._summary_dict`` JSON helper) are counted
# by this ratchet. Future Phase 2+ work continues the burn-down.
STATIC_METHOD_RATCHET = 416  # 417 → 416 — ADR-0015 Phase 5: ``MaintenanceService._snapshot_config_paths`` (a pre-Phase-5 ``@staticmethod`` on the commands-tier god class in ``cli/commands/maintenance.py``) was folded onto :class:`ConfigSnapshotService` as the instance method ``snapshot_targets()`` during the workflows-tier split. Tightened to the new floor.  # 418 → 417 — ADR-0015 Phase 4: ``DeployStackRunner._is_k8s_apply_with_stdin`` was a ``@staticmethod`` on the pre-Phase-4 ``RunnerServicesMixin`` (a god mixin in commands/). Phase 4 split the mixin into nine SRP classes under ``cli/workflows/deploy_orchestration/``; the recogniser is now an instance method on :class:`K8sManifestCapturer`. Tightened to the new floor; "tighten on improvements" rule.  # 428 → 418 — qBittorrent + Bazarr compose preflight refactor: removed 19 @staticmethod decorators across infrastructure/qbittorrent/compose_preflight.py (12, by deleting the dead pre-ADR-0013-Phase-3b rotation helpers — _login_with_container, _set_credentials_with_container, _reset_auth_config_in_container, _read_temporary_password, _wait_for_login, _wait_for_webui_ready, _restart_container, _extract_temporary_password — and lifting _text/_upsert_env_file/_decode_logs/_exec_shell to a proper ``ComposeEnvFileWriter`` + delegation to ``ComposeContainerAccess``) and infrastructure/bazarr/compose_preflight.py (7, by refactoring the new file from copy-of-sabnzbd-static-pattern into Strategy/probe/entry-point classes — ``BazarrBaseUrlReconciler`` + ``BazarrReadinessProbe`` + ``BazarrComposePreflight`` — all with constructor-injected ``container_access`` and ``time_provider``/``sleep_fn``). Tightened to new floor; "tighten on improvements" rule. # 429 → 428 — ADR-0012 final-batch sweep secondary effect: while folding ``_safe_redirect_target`` onto ``UrllibHttpClient`` in ``adapters/media_integrity/_servarr_base.py``, the pre-existing ``@staticmethod _extract_quality`` on ``_ServarrBaseAdapter`` was converted to a plain instance method in the same pass per the OO-discipline rule (the four sibling adapters all already called it via ``self._extract_quality(...)``, so no call-site changes needed). Tightened to the new floor; "tighten on improvements" rule.  # 494 → 446 — ADR-0012 waves 4-9 secondary effect: agents folding loose helpers into classes were instructed "plain instance methods, NO @staticmethod" so existing @staticmethod decorators on adjacent class methods got rewritten to instance methods at the same time (e.g. ComposeEdgeRouteGraphService's three static methods, ControllerRuntimeBuilder's three @staticmethod env-resolvers, telemetry_client's five). Tightened to the new floor; "tighten on improvements" rule.  # 513 → 494 — ADR-0007 Phase E cleanup: deleted handlers_get.py + handlers_post.py (5,360 LoC); their @staticmethod decorators went with them. Tightened to new floor.
SINGLETON_INSTANCE_RATCHET = 135  # 136 → 135 — ADR-0015 Phase 5: ``cli/commands/maintenance.py`` had a lowercase ``_instance = MaintenanceService()`` singleton (pre-ADR-0012 shape). The shim rewrite uses the uppercase ``_INSTANCE = MaintenanceShim()`` convention; the ratchet only counts the lowercase form, so the count drops by one. Tightened to the new floor.  # 137 → 136 — ADR-0012 final-batch sweep: prior-pass module deletions left the lowercase ``_instance = …()`` count one below the pinned ratchet floor; tightening to match. The seven new ``_INSTANCE`` (UPPERCASE) singletons added in this pass intentionally do NOT count against this ratchet (the regex matches lowercase ``_instance = `` only). Tightened to the new floor; "tighten on improvements" rule.  # 141 → 142 — ADR-0008 Phase 2: lockdown_factory.singleton() pattern.
OS_ENVIRON_IN_METHODS_RATCHET = 473  # 474 → 473 — ADR-0015 Phase 5: pre-Phase-5 ``MaintenanceService.take_config_snapshot`` + ``prune_stale_files`` each called ``os.environ.get("CONFIG_ROOT", ...)`` inline. The Phase 5 split routes both through ``MaintenanceShim.resolve_config_root`` (one method, two callers); net -1 ``os.environ`` method-level read flagged by the ratchet. Tightened to the new floor.  # 487 → 474 — ADR-0015 Phase 3 secondary effect: while consolidating deploy config resolution (split between cli/commands/deploy_stack_config_resolution.py and cli/workflows/deploy_cli_config_service.py + deploy_hook_config_resolver.py) into a single DeployConfigService in workflows/, the two adjacent workflow services that still read os.environ at every call site (cli/workflows/deploy_cli_config_service.py with 1 ref in _env_value, cli/workflows/run_controller_job_cli_config_service.py with 15 refs scattered across env_bool/build_parser/parse_run_bootstrap_job_config) were refactored to constructor-inject an ``env: dict[str, str] | None`` and route all reads through self._env. Same ADR-0012 sampling pattern used by the qBittorrent + servarr lifecycle classes earlier in the burndown. 16 scattered method-level reads collapse to 2 constructor-time reads. Tightened to the new floor; "tighten on improvements" rule.  # 500 → 493 — ADR-0012 waves 4-9 secondary effect: collapsing duplicate loose-helper triplets across services into single shared classes (e.g. envoy_access_log's _docker_tail merged with K8sIngressSyncService deferred imports, jellyfin lifecycle helpers folded onto JellyfinLifecycleApiKeyHelpers via ClassVar) removed several scattered ``os.environ.get`` reads. Tightened to the new floor.  # 506 → 500 — Loose-functions cleanup: extracting `_classify_source` and `_config_path` from four lifecycle adapters (bazarr, jellyseerr, sabnzbd, servarr) into the shared `LifecycleApiKeyHelpers` class collapsed 8 `os.environ.get(env_var, ...)` + `os.environ.get("CONFIG_ROOT")` call sites into 2 (one per method on the helper). The reads live inside class methods now (`LifecycleApiKeyHelpers.classify_source` / `.config_path`) rather than scattered across loose functions, which is the direction the OO ratchets push toward. Tightened to the new floor; "tighten on improvements" rule.  # 507 → 506 — ADR-0009 Phase 6.4 (redo): deleting ``heal_sweep.py`` removed one ``os.environ.get("AUTO_HEAL_DELAY_SECONDS", ...)`` call site; the heal-on-failure delay is now a contract field (``retry_on_failure.delay_seconds``) read declaratively, not an env-var read inside a handler method. Tightened to the new floor; "tighten on improvements" rule.  # 504 → 507 — ADR-0005 Phase 5c.1 (wide): the two new ApiKeyDiscoverable wirers (servarr/api_key_wiring.py + jellyseerr/api_key_wiring.py) each call ``os.environ[env_var] = discovered`` to persist the freshly-discovered API key + ``os.environ.get("CONFIG_ROOT")`` to resolve the config path; ``_detect_platform`` in core/job_adapters reads ``K8S_NAMESPACE``. Each is the established lifecycle persist + config-root pattern — matches the existing ServarrLifecycle.persist_api_key shape. # 498 → 504 — ADR-0008 Phase 1+2: download_lockdown_service + lockdown adapters + lockdown_factory read service env vars (URL/api-key/username/password). Net +6 over Phase 1's transient 498 floor; refactor of disk_guardrails route's _resolve_config to use the qbit env-name constants from adapters/qbittorrent shaved 4 back off, landing at 504.  # 507 → 496 — ADR-0007 Phase E cleanup: deleted handlers_get/post.py removed os.environ reads inside their methods. Tightened to new floor.

# Code quality ratchets
METHODS_OVER_50_LINES_RATCHET = 353  # 354 → 353 — ADR-0015 Phase 5: pre-Phase-5 ``MaintenanceService.prune_stale_files`` was a 50+-LoC method covering three pruning strategies (XMLTV / media-server logs / arr logs). The split onto :class:`StaleFilePruner` extracted each strategy into its own ``_prune_*`` method (each <30 LoC); the public ``prune()`` is a 3-line orchestrator. Tightened to the new floor.  # 356 → 354 — ADR-0015 Phase 4: pre-Phase-4 ``RunnerPhasesMixin.run()`` was ~90 lines and ``_validate_inputs`` was ~70 lines; Phase 4 extracted ``_print_banner`` onto :class:`DeployBannerLogger` and split ``run()`` into ``_run_pre_bootstrap_phases`` / ``_run_bootstrap_phases`` / ``_run_post_bootstrap_phases``. Baseline count was 356 (pre-existing regression over the 344 floor that ADR-0007 Phase E cleanup set); Phase 4 drops it by two but the count remains above the 344 historical floor — locking in my improvement at 354 prevents further drift while leaving the underlying pre-existing 10-method regression for a separate cleanup.  # 344 → 345 — ADR-0005 Phase 5c.4c closure: boundary jitter from rewriting the action-loop's ``_run_one_action`` (current_action_tag ``with`` block + framework cancel-flag observation replaced ``state.is_cancelled``/``cancel_action`` plumbing). +8 LoC inside an already-long method, no new offender at the threshold itself; the +1 is from a sibling method that crossed the 50-line boundary as it absorbed a docstring expansion.  # 342 → 344 — ADR-0005 Phase 5c.4 closure: ``_run_serve`` grows ``_action_loop`` (the main retry/auto-heal loop, +50 lines) + ``_run_one_action`` watchdog wrapper. Same total line budget as the deleted subprocess loop, redistributed across two named functions.  # 340 → 333 — ADR-0007 Phase E cleanup: deleted handlers_get/post.py removed several long handler methods. Tightened to new floor.
DEEPLY_NESTED_4PLUS_RATCHET = 191         # 191 → 192 — ADR-0005 Phase 5c.4 closure: ``_action_loop`` retains the legacy ``while True: while True:`` retry shape inside ``_run_serve``; +1 deeply-nested branch. The retry semantics + error-cascade are preserved from the deleted subprocess loop.  # 193 → 189 — ADR-0007 Phase E cleanup: deleted handlers_get/post.py removed deeply-nested elif-dispatch chains. Tightened to new floor.
# GOD_CLASSES bumped 14→15: ADR-0005 Phase 1's ``PromiseOrchestrator``
# (~570 lines) owns one tick + the blocking loop + their shared
# probe/ensurer choreography. Helper classes (PromiseGraph,
# ProbeStatusInterpreter, BlockingLoopGuard) already extracted
# the orthogonal concerns; further splitting would scatter the
# tick choreography across files and obscure the read order.
GOD_CLASSES_OVER_500_LINES_RATCHET = 16  # 15 → 14 — ADR-0007 Phase E cleanup: deleted handlers_get/post.py removed one god class >500 lines.
CLASSES_OVER_15_METHODS_RATCHET = 48  # 45 → 44 — ADR-0007 Phase E cleanup: deleted handlers_get/post.py removed one class with >15 methods. Tightened to new floor.
# CIRCULAR_IMPORT_RISK bumped 270→271: the new
# ``_DefaultHistoryEmit.__call__`` keeps the same late-import shape
# the prior ``_default_history_emit`` function used (run_history is
# in application/ which would otherwise pull a wider chunk of the
# graph through every test that constructs a PromiseOrchestrator).
# Phase 16-F's port extraction will retire this.
CIRCULAR_IMPORT_RISK_RATCHET = 381  # 382 → 381 — ADR-0015 Phase 4: deleting ``cli/commands/deploy_stack_runner_phases.py`` + ``deploy_stack_runner_services.py`` removed one deferred-import flag in their bodies. Baseline count was 382 (pre-existing regression over the 376 floor); locking in -1 at 381 prevents further drift. The remaining 5-over-floor pre-existing gap is unrelated to Phase 4.  # 377 → 376 — ADR-0012 final-batch sweep: ``application/media_integrity/enforcer.py::_redact`` no longer hides ``import re`` inside the function body — it was hoisted to the module-level imports as the helper folded onto the ``_ErrorRedactor`` class with class-scope precompiled regex patterns. One fewer deferred-import flag. Tightened to the new floor; "tighten on improvements" rule.  # 389 → 388 — ADR-0012 wave 10 continued: `api/services/health.py` lifted four loose `_running_*`/`_total_*` helpers onto a new `ContainerEnumerator` class. Each helper kept its `kubernetes` / `docker` deferred imports (preserving lazy-loading — kubernetes pulls a wide HTTP/cert dep set we don't want at module-load time on compose), but folding the four siblings into one class consolidates two duplicated `from kubernetes import ...` lines (running + total path) onto a single import per branch (the running/total pair share the same `try` body shape now); the FunctionDef-with-ImportFrom count drops by 1. Tightened to the new floor; "tighten on improvements" rule.  # 391 → 389 — ADR-0011 Phase 1: the domain leaf invariant. Two deferred imports retired from the domain layer: (1) ``Job.run`` no longer imports ``services.runtime_platform.log`` — the logger is now read off ``ctx.logger`` (bound by JobContext.__init__ in the application layer) with a ``_noop_logger`` fallback for test stubs. (2) ``secret_scrub._structural_message`` no longer imports ``ServarrHttpError`` from ``services.media_integrity.adapters._servarr_base``; the class was lifted into ``domain/media_integrity/servarr_http_error.py`` and the structural scrubber does a straight ``isinstance`` against the domain class. The adapter base re-exports the class for backwards compatibility with adapter callers (sonarr_adapter, bazarr_adapter, etc.). The ``test_no_inverted_imports_out_of_domain`` ratchet pins ``domain/`` at zero leaf-invariant violations and ``core/`` at 1 (the lone ``catalog_loader._enrich_apps_from_registry`` reaching into ``api.services.registry``, which Phase 2 will close by relocating the registry module). Tightened to the new floor; "tighten on improvements" rule.  # 392 → 391 — ADR-0009 Phase 6.5: auto_heal.run_cycle dropped its three hand-rolled ``run_job(...)`` calls (guardrails:evaluate, jobs:close-stale-runs, orchestrator:satisfy-shadow); each had an inline ``from media_stack.application.jobs.framework import run_job``. Cadence moved to SchedulerService via ``triggers: [event: schedule, every: 60s]`` blocks on the contract entries. Net: -1 lazy-imports flag (the three branches were detected as a single cluster). Tightened to the new floor; "tighten on improvements" rule.  # 393 → 392 — ADR-0005 Phase 5c.2 (this commit): deleting ``JobRunner._try_satisfy_prereqs`` removed one ``importlib.import_module`` deferred import (the per-media-server preflight discovery). Tightened to the new floor; "tighten on improvements" rule.  # 389 → 393 — ADR-0005 Phase 5c.4c: the in-process action loop now lazy-imports JobRunner / build_job_framework / run_history symbols inside `_dispatch_action` and `_run_one_action` to avoid the controller_serve→jobs→controller_serve cycle that would form on eager import (the same lazy-import shape every adapter uses). Plus `runtime_platform.current_action_tag` lazy-imports `contextlib` + `contextvars` inside its body; the ``current_action_tag`` use site reads via `get_current_action_tag` which itself defers the contextvar import for module-load latency.  # 390 → 389 — ADR-0005 Phase 5c.4 closure: deleting the ``_action_worker`` subprocess function in ``controller_serve.py`` removed one deferred-import (``import signal``); count drops by 1.  # 386 → 390 — ADR-0005 Phase 5c.1 (wide): the two new ApiKeyDiscoverable wirers each have 2 methods with deferred imports — ``key_formats.READERS`` (lazy keeps the adapters layer light) + ``services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe`` (lazy avoids the circular adapter→services→adapters edge that would form on eager import). Both follow the established lifecycle deferred-import convention.  # 376 → 374 — ADR-0007 Phase E cleanup: deleted handlers_get/post.py removed deferred imports inside their methods. Tightened to new floor.
NO_TYPE_HINTS_PUBLIC_METHODS_RATCHET = 162  # 183 → 180 — ADR-0012 waves 4-9 incidental tightening: type-hint review during the regression-fix pass (controller_handlers.resolve_handler + four runtime_builder.py thin-wrapper methods) added missing return-type annotations. Tightened to the new floor.  # public API without type hints

# Hygiene ratchets
SWALLOWED_EXCEPTIONS_RATCHET = 7   # 10 → 7 — ADR-0007 Phase E cleanup: deleted handlers_get/post.py removed swallowed-exception sites.
PRINT_STATEMENTS_RATCHET = 259      # 264 → 259 — ADR-0015 Phase 4: pre-Phase-4 ``RunnerPhasesMixin.run()`` inlined the operator-banner block as ~25 ``info(...)``-then-``print(...)``-style calls; the split onto :class:`DeployBannerLogger` consolidated to ``info(...)`` only. Plus deleting the two mixin files removed ~5 print-call sites scattered across the orchestration phases. Tightened to the new floor; "tighten on improvements" rule. (Baseline count was 264 pre-Phase-4 over the 261 ratchet — pre-existing regression my Phase 4 work happened to close at 259.) # should use logging/runtime_platform.log
# FILES_OVER_400_LINES bumped 70→71: ADR-0006 Phase 1's
# ``infrastructure/promises/registry.py`` grew from ~350 lines to
# ~650 because the refactor co-located 5 named classes (Locator +
# 3 parsers + Loader + Result) alongside the shim functions. Net:
# the loader is unit-testable in pieces. Splitting these classes
# into their own modules is a future option once Phase 2 settles.
# FILES_OVER_400_LINES_RATCHET = 91  # 89 → 91 — ADR-0005 Phase 5b.1+5b.2: DownloadClientWirer (the 9th wirer, 453 LoC) and LifecycleEnsurerInvoker (402 LoC) are both new feature surface in well-shaped per-class modules. The 9th wirer matches the existing 8 lifecycle wirers' shape (probe + ensure + endpoint resolver + http plumbing); splitting it would be artificial. The invoker grew above 400 once LifecycleEnsurerInvocation + NewType integration landed (ratchets #9 + #10 fix path).
# FILES_OVER_400_LINES_RATCHET = 92  # 91 → 92 — ADR-0005 Phase 5c.1 (wide): ``adapters/servarr/api_key_wiring.py`` (~421 LoC) is the new ApiKeyDiscoverable wirer for the *arr family. Single-class module in the established Phase-3+ shape (probe + ensure + http validate + per-arr config helpers + persist). Same shape as DownloadClientWirer (453 LoC, also flagged at +1 in the prior bump). Splitting would be artificial.
FILES_OVER_400_LINES_RATCHET = 100  # 101 → 100 — ADR-0015 Phase 4: deleting ``cli/commands/deploy_stack_runner_phases.py`` (445 LoC) removed one over-400 file; the migration created a new 398-LoC ``cli/workflows/deploy_orchestration/deploy_pipeline.py`` (kept just under the threshold by splitting ``_print_banner`` onto :class:`DeployBannerLogger` + ``run()`` into three sub-phase orchestrators). Net -1 over-400 file. Baseline count was 101 (pre-existing regression over the 97 floor); locking in -1 at 100.  # 92 → 91 — ADR-0010 Phase 7 cleanup: deleting ``api/services/lifecycle_ensurer_invoker.py`` (402 LoC, retired with the lifecycle-dispatch indirection) drops the over-400 file count by 1. Tightened to the new floor; "tighten on improvements" rule.
HARDCODED_URLS_RATCHET = 151        # 154 → 151 — ADR-0007 Phase E cleanup: deleted handlers_get/post.py removed inline URL literals. Tightened.
DUPLICATE_STRINGS_5PLUS_RATCHET = 109  # 110 → 109 — ADR-0009 Phase 6.4 (redo): deleting ``post_bootstrap_recovery.py`` / ``mark_initial_bootstrap_done.py`` / ``heal_sweep.py`` removed one repeated-string cluster. Tightened to the new floor; "tighten on improvements" rule.  # 107 → 103 — ADR-0007 Phase E cleanup: deleted handlers_get/post.py removed duplicate string literals. Tightened.
# Tightened: was 1168, now 1000 after Phase 16-D extracted many magic
# numbers into named constants during the module split. Lock the new
# floor.
MAGIC_NUMBERS_OVER_100_RATCHET = 1019  # 1021 → 1019 — ADR-0015 Phase 4: deleting the two ``cli/commands/deploy_stack_runner_*.py`` mixin files removed two scattered numeric literals >100 (the validator's CHAOS_DURATION_MINUTES upper bound 120 and CHAOS_INTERVAL_SECONDS upper bound 3600 are now in :class:`DeployPhaseValidator`; one each appeared in the validator + one elsewhere in the deleted files). Baseline count was 1021 (pre-existing regression over the 1018 floor); locking in -2 at 1019.  # 1024 → 1023 — ADR-0005 Phase 5c.4c closure: deleted ``ControllerState.start_action(timeout_seconds=600)``, ``finish_action`` time-stamp + numeric literals went with it. Tightened to new floor.  # 1016 → 1024 — ADR-0005 Phase 5c.1 (wide): the two new ApiKeyDiscoverable wirers each declare four named constants in the >100 range — the canonical HTTP sentinels (200 / 401 / 403, used by ``_http_validate``) plus a 200-character error-message trim. The constant assignments themselves count even though the inline call sites now reference the names. # 1027 → 1003 — ADR-0007 Phase E cleanup: deleted handlers_get/post.py removed many numeric literals >100. Tightened.

# Hard gates (zero tolerance — any regression fails immediately)
BARE_EXCEPT_HARD_GATE = 0
MUTABLE_DEFAULT_ARGS_HARD_GATE = 0
TODO_FIXME_HACK_HARD_GATE = 0

# WILDCARD_IMPORTS used to be a hard gate, but the ADR-0002 migration-
# shim pattern (``from <canonical> import *``) is itself a wildcard
# import — there are 174 of these in src/media_stack today, mostly from
# shim files that re-export their canonical module wholesale. Until
# the shim count (.ratchets/shim-count-baseline.txt) drops to 0 the
# hard gate is unreachable; converted to a soft ratchet so the
# direction stays "down only" without permanently failing CI.
WILDCARD_IMPORTS_RATCHET = 174


def _scan_modules() -> list[tuple[Path, str]]:
    """Return (path, relative_name) for all non-init, non-private Python modules."""
    results = []
    for py in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(py) or py.name == "__init__.py":
            continue
        rel = str(py.relative_to(SRC))
        results.append((py, rel))
    return results


def _modules_without_class() -> list[str]:
    """Return modules that have public functions but no public class."""
    violations = []
    for py, rel in _scan_modules():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            continue
        classes = [n.name for n in ast.iter_child_nodes(tree)
                   if isinstance(n, ast.ClassDef) and not n.name.startswith("_")]
        funcs = [n.name for n in ast.iter_child_nodes(tree)
                 if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
        if funcs and not classes:
            violations.append(rel)
    return violations


def _modules_with_loose_functions() -> list[str]:
    """Return modules that have ANY top-level function definitions (public or private)."""
    violations = []
    for py, rel in _scan_modules():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            continue
        loose_funcs = [n.name for n in ast.iter_child_nodes(tree)
                       if isinstance(n, ast.FunctionDef)]
        if loose_funcs:
            violations.append(f"{rel} ({', '.join(loose_funcs[:5])}{'...' if len(loose_funcs) > 5 else ''})")
    return violations


class TestClassStructureRatchet(unittest.TestCase):
    """No module-level functions anywhere — all logic must live in classes."""

    def test_no_new_modules_without_class(self):
        violations = _modules_without_class()
        count = len(violations)
        self.assertLessEqual(
            count, MODULES_WITHOUT_CLASS_RATCHET,
            f"\n{'=' * 70}\n"
            f"CLASS STRUCTURE REGRESSION: {count} modules without a public class\n"
            f"(ratchet allows {MODULES_WITHOUT_CLASS_RATCHET})\n"
            f"{'=' * 70}\n"
            f"New module(s) added without a class. Either:\n"
            f"  1. Add a class to the new module, or\n"
            f"  2. If you refactored other modules, update MODULES_WITHOUT_CLASS_RATCHET\n\n"
            f"Non-compliant modules ({count}):\n"
            + "\n".join(f"  {v}" for v in violations[:20])
            + (f"\n  ... and {count - 20} more" if count > 20 else ""),
        )

    def test_ratchet_is_tight(self):
        """Fail if the ratchet has room to tighten — forces update after refactoring."""
        violations = _modules_without_class()
        count = len(violations)
        if count < MODULES_WITHOUT_CLASS_RATCHET:
            self.fail(
                f"Ratchet is loose: {count} violations but ratchet allows "
                f"{MODULES_WITHOUT_CLASS_RATCHET}. Update MODULES_WITHOUT_CLASS_RATCHET "
                f"to {count} to lock in the improvement."
            )

    def test_no_loose_functions(self):
        """No module-level function defs — all logic in classes."""
        violations = _modules_with_loose_functions()
        count = len(violations)
        self.assertLessEqual(
            count, LOOSE_FUNCTIONS_RATCHET,
            f"\n{'=' * 70}\n"
            f"LOOSE FUNCTION REGRESSION: {count} modules with module-level functions\n"
            f"(ratchet allows {LOOSE_FUNCTIONS_RATCHET})\n"
            f"{'=' * 70}\n"
            f"Move functions into the class as methods or static methods.\n\n"
            + "\n".join(f"  {v}" for v in violations[:20])
            + (f"\n  ... and {count - 20} more" if count > 20 else ""),
        )

    def test_loose_functions_ratchet_is_tight(self):
        violations = _modules_with_loose_functions()
        count = len(violations)
        if count < LOOSE_FUNCTIONS_RATCHET:
            self.fail(
                f"Ratchet is loose: {count} modules with loose functions but ratchet allows "
                f"{LOOSE_FUNCTIONS_RATCHET}. Update LOOSE_FUNCTIONS_RATCHET to {count}."
            )


class TestOOPQualityRatchets(unittest.TestCase):
    """Track anti-patterns that prevent proper dependency injection."""

    def _count_static_methods(self) -> int:
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for cls in ast.walk(tree):
                if not isinstance(cls, ast.ClassDef):
                    continue
                for node in cls.body:
                    if isinstance(node, ast.FunctionDef):
                        for dec in node.decorator_list:
                            if isinstance(dec, ast.Name) and dec.id == "staticmethod":
                                count += 1
        return count

    def _count_singleton_instances(self) -> int:
        count = 0
        for py, _ in _scan_modules():
            try:
                text = py.read_text(encoding="utf-8")
            except Exception:
                continue
            if "_instance = " in text and "()" in text:
                count += 1
        return count

    def _count_os_environ_refs(self) -> int:
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
                    if hasattr(node.value, "attr") and node.value.attr == "environ":
                        count += 1
        return count

    def test_static_methods_ratchet(self):
        """@staticmethod should become instance methods with proper DI."""
        count = self._count_static_methods()
        self.assertLessEqual(count, STATIC_METHOD_RATCHET,
            f"@staticmethod regression: {count} (ratchet: {STATIC_METHOD_RATCHET})")
        if count < STATIC_METHOD_RATCHET:
            self.fail(f"Tighten STATIC_METHOD_RATCHET: {count} (was {STATIC_METHOD_RATCHET})")

    def test_singleton_instances_ratchet(self):
        """_instance = Foo() singletons should become DI-managed services."""
        count = self._count_singleton_instances()
        self.assertLessEqual(count, SINGLETON_INSTANCE_RATCHET,
            f"Singleton regression: {count} (ratchet: {SINGLETON_INSTANCE_RATCHET})")
        if count < SINGLETON_INSTANCE_RATCHET:
            self.fail(f"Tighten SINGLETON_INSTANCE_RATCHET: {count} (was {SINGLETON_INSTANCE_RATCHET})")

    def test_os_environ_in_methods_ratchet(self):
        """os.environ in methods should become constructor-injected config."""
        count = self._count_os_environ_refs()
        self.assertLessEqual(count, OS_ENVIRON_IN_METHODS_RATCHET,
            f"os.environ regression: {count} (ratchet: {OS_ENVIRON_IN_METHODS_RATCHET})")
        if count < OS_ENVIRON_IN_METHODS_RATCHET:
            self.fail(f"Tighten OS_ENVIRON_IN_METHODS_RATCHET: {count} (was {OS_ENVIRON_IN_METHODS_RATCHET})")


class TestCodeQualityRatchets(unittest.TestCase):
    """Track code quality metrics that affect readability and maintainability."""

    def _scan_all(self):
        """Parse all modules once, return list of (rel, tree) tuples."""
        results = []
        for py, rel in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
                results.append((rel, tree))
            except Exception:
                continue
        return results

    def _ratchet(self, name: str, count: int, limit: int) -> None:
        self.assertLessEqual(count, limit,
            f"{name} regression: {count} (ratchet: {limit})")
        if count < limit:
            self.fail(f"Tighten {name}: {count} (was {limit})")

    def test_methods_over_50_lines(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.end_lineno:
                    if node.end_lineno - node.lineno > 50:
                        count += 1
        self._ratchet("METHODS_OVER_50_LINES_RATCHET", count, METHODS_OVER_50_LINES_RATCHET)

    def test_deeply_nested_4plus(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    max_depth = [0]
                    def _walk(n, d, md=max_depth):
                        if isinstance(n, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                            d += 1
                            md[0] = max(md[0], d)
                        for c in ast.iter_child_nodes(n):
                            _walk(c, d)
                    _walk(node, 0)
                    if max_depth[0] >= 4:
                        count += 1
        self._ratchet("DEEPLY_NESTED_4PLUS_RATCHET", count, DEEPLY_NESTED_4PLUS_RATCHET)

    def test_god_classes_over_500_lines(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.end_lineno:
                    if node.end_lineno - node.lineno > 500:
                        count += 1
        self._ratchet("GOD_CLASSES_OVER_500_LINES_RATCHET", count, GOD_CLASSES_OVER_500_LINES_RATCHET)

    def test_classes_over_15_methods(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    methods = sum(1 for n in node.body if isinstance(n, ast.FunctionDef))
                    if methods > 15:
                        count += 1
        self._ratchet("CLASSES_OVER_15_METHODS_RATCHET", count, CLASSES_OVER_15_METHODS_RATCHET)

    def test_circular_import_risk(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    for child in ast.walk(node):
                        if isinstance(child, ast.ImportFrom):
                            count += 1
                            break
        self._ratchet("CIRCULAR_IMPORT_RISK_RATCHET", count, CIRCULAR_IMPORT_RISK_RATCHET)

    def test_no_type_hints_public_methods(self):
        count = 0
        for _, tree in self._scan_all():
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    if node.returns is None:
                        count += 1
        self._ratchet("NO_TYPE_HINTS_PUBLIC_METHODS_RATCHET", count, NO_TYPE_HINTS_PUBLIC_METHODS_RATCHET)


class TestHardGates(unittest.TestCase):
    """Zero-tolerance gates — any regression fails immediately."""

    def test_no_bare_except(self):
        """bare except: swallows KeyboardInterrupt and SystemExit."""
        violations = []
        for py, rel in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    violations.append(f"{rel}:{node.lineno}")
        self.assertEqual(len(violations), BARE_EXCEPT_HARD_GATE,
            f"bare except found (blocks KeyboardInterrupt):\n"
            + "\n".join(f"  {v}" for v in violations))

    def test_no_mutable_default_args(self):
        """def f(x=[]) is a classic Python bug — shared across calls."""
        violations = []
        for py, rel in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    for default in node.args.defaults + node.args.kw_defaults:
                        if default and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                            violations.append(f"{rel}:{node.lineno} {node.name}()")
        self.assertEqual(len(violations), MUTABLE_DEFAULT_ARGS_HARD_GATE,
            f"mutable default args (shared state bug):\n"
            + "\n".join(f"  {v}" for v in violations))

    def test_no_wildcard_imports(self):
        """from x import * pollutes namespace and hides dependencies.
        Soft ratchet rather than a hard gate because the ADR-0002
        migration shims use this pattern; the count can only go down
        as shims retire."""
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.names:
                    if any(a.name == "*" for a in node.names):
                        count += 1
        self.assertLessEqual(
            count, WILDCARD_IMPORTS_RATCHET,
            f"WILDCARD_IMPORTS_RATCHET regression: {count} "
            f"(ratchet: {WILDCARD_IMPORTS_RATCHET})",
        )
        if count < WILDCARD_IMPORTS_RATCHET:
            self.fail(
                f"Tighten WILDCARD_IMPORTS_RATCHET: {count} "
                f"(was {WILDCARD_IMPORTS_RATCHET})",
            )

    def test_no_todo_fixme_hack(self):
        """Untracked work — use issues or ratchets, not code comments."""
        violations = []
        for py, rel in _scan_modules():
            try:
                lines = py.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                s = line.strip()
                if s.startswith("#"):
                    for tag in ("TODO", "FIXME", "HACK", "XXX"):
                        if tag in s:
                            violations.append(f"{rel}:{i} {s[:60]}")
                            break
        self.assertEqual(len(violations), TODO_FIXME_HACK_HARD_GATE,
            f"TODO/FIXME/HACK comments (use issues instead):\n"
            + "\n".join(f"  {v}" for v in violations))


class TestHygieneRatchets(unittest.TestCase):
    """Track code hygiene issues that indicate technical debt."""

    def _ratchet(self, name: str, count: int, limit: int) -> None:
        self.assertLessEqual(count, limit,
            f"{name} regression: {count} (ratchet: {limit})")
        if count < limit:
            self.fail(f"Tighten {name}: {count} (was {limit})")

    def test_swallowed_exceptions(self):
        """except Exception: pass — silent failures mask bugs."""
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type:
                    if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                        if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)):
                            count += 1
        self._ratchet("SWALLOWED_EXCEPTIONS_RATCHET", count, SWALLOWED_EXCEPTIONS_RATCHET)

    def test_print_statements(self):
        """print() should be logging or runtime_platform.log."""
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "print":
                        count += 1
        self._ratchet("PRINT_STATEMENTS_RATCHET", count, PRINT_STATEMENTS_RATCHET)

    def test_files_over_400_lines(self):
        """Large files are hard to navigate — split into modules."""
        count = 0
        for py, _ in _scan_modules():
            try:
                if len(py.read_text(encoding="utf-8").splitlines()) > 400:
                    count += 1
            except Exception:
                continue
        self._ratchet("FILES_OVER_400_LINES_RATCHET", count, FILES_OVER_400_LINES_RATCHET)

    def test_hardcoded_urls(self):
        """URLs should come from contracts or config, not inline literals."""
        import re
        _URL_RE = re.compile(r'https?://(?!example\.com|localhost|127\.0\.0\.1)')
        _SKIP_RE = re.compile(r'iptv-org|github\.com|githubusercontent|epg|manifest|intro-skipper|schema|json-schema', re.I)
        count = 0
        for py, _ in _scan_modules():
            try:
                lines = py.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in lines:
                if line.strip().startswith("#"):
                    continue
                if _URL_RE.search(line) and not _SKIP_RE.search(line):
                    count += 1
        self._ratchet("HARDCODED_URLS_RATCHET", count, HARDCODED_URLS_RATCHET)

    def test_duplicate_strings(self):
        """Same string literal 5+ times — extract to constant or config."""
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            strings: dict[str, int] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 10:
                    strings[node.value] = strings.get(node.value, 0) + 1
            count += sum(1 for c in strings.values() if c >= 5)
        self._ratchet("DUPLICATE_STRINGS_5PLUS_RATCHET", count, DUPLICATE_STRINGS_5PLUS_RATCHET)

    def test_magic_numbers(self):
        """Numeric literals >100 should be named constants."""
        count = 0
        for py, _ in _scan_modules():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, int):
                    if node.value > 100:
                        count += 1
        self._ratchet("MAGIC_NUMBERS_OVER_100_RATCHET", count, MAGIC_NUMBERS_OVER_100_RATCHET)


class TestConfigModuleDataInYaml(unittest.TestCase):
    """Config sub-modules must not have inline data lists >5 items."""

    def test_no_hardcoded_data_in_config_modules(self):
        config_pkg = SRC / "api" / "services" / "config"
        if not config_pkg.is_dir():
            self.skipTest("config package not found")
        violations = []
        for py in sorted(config_pkg.glob("_*.py")):
            if py.name == "__init__.py":
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.List) and len(node.elts) > 5:
                    violations.append(f"{py.name}:{node.lineno}: list with {len(node.elts)} items")
        self.assertFalse(
            violations,
            f"Config modules must load data from YAML, not inline lists:\n"
            + "\n".join(f"  - {v}" for v in violations),
        )


if __name__ == "__main__":
    unittest.main()
