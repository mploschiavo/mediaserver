#!/usr/bin/env python3
"""Image-smoke ratchet: boot every controller subsystem at image-build time.

Wired into the Dockerfile after ``pip install``. Failure here means the
image is broken — don't ship it.

v1.0.231 incident: media-integrity silently disabled at boot because a
path candidate was missing. The wheel image's
``/opt/media-stack/contracts/`` was not in the loader's hardcoded list,
so ``ServarrPolicy.load_default()`` raised ``FileNotFoundError``,
``build_default_service`` was never wired, and the operator saw
"media-integrity service not configured" with no obvious cause. This
smoke catches that class for every subsystem it covers.

How it works
------------
For each well-known controller subsystem, this script runs the public
boot/factory entry point, captures any exception (including chained
``__cause__``), and prints a per-subsystem status table at the end.
Any failure causes the script to exit non-zero so the Docker layer
fails the build.

What's intentionally out of scope
---------------------------------
* No network calls. We only verify the *boot* path — adapters that need
  a live host do their actual probes elsewhere. The smoke is meant to
  run in <30s so it's tolerable on every image build.
* No mutation of on-disk state. The smoke must be idempotent.
* No user/credential setup. Each subsystem either constructs cleanly
  with empty/default inputs, or it gracefully degrades to a "not
  configured" posture (which is a SUCCESS for smoke purposes — the
  point is that import + initialise didn't crash).

Why each subsystem is in the list — bug-history map
---------------------------------------------------
Each entry below names the bug class the smoke would have caught.
Add new entries as new "look here, fall back to there" path-resolution
patterns land. The companion ratchet
``tests/unit/architecture/test_image_smoke_coverage.py`` enforces a
floor count and surfaces uncovered ``factory.py`` modules.
"""

from __future__ import annotations

import importlib
import os
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Callable


# Make sure the repo's src/ is importable when running from a dev tree
# (the wheel install puts ``media_stack`` on sys.path automatically, so
# this is a no-op inside the image).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SRC = os.path.join(_REPO_ROOT, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)


@dataclass
class SmokeResult:
    name: str
    status: str  # "ok" | "skip" | "fail"
    duration_ms: int
    detail: str = ""


def _format_chained(exc: BaseException) -> str:
    """Render an exception including chained ``__cause__`` /
    ``__context__`` so loaders that swallow-and-rethrow surface their
    root cause in the smoke output."""
    parts: list[str] = []
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        tb_lines = traceback.format_exception_only(type(cur), cur)
        parts.append("".join(tb_lines).strip())
        cur = cur.__cause__ or cur.__context__
    return "\n  caused by: ".join(parts)


def _run(name: str, fn: Callable[[], object], *, why: str) -> SmokeResult:
    """Execute one subsystem boot, capturing duration + chained error."""
    t0 = time.monotonic()
    try:
        fn()
    except _Skip as skip:
        dur = int((time.monotonic() - t0) * 1000)
        return SmokeResult(name=name, status="skip", duration_ms=dur, detail=str(skip))
    except BaseException as exc:  # noqa: BLE001 — smoke wants every failure
        dur = int((time.monotonic() - t0) * 1000)
        return SmokeResult(
            name=name,
            status="fail",
            duration_ms=dur,
            detail=f"{_format_chained(exc)}\n[why this matters] {why}",
        )
    dur = int((time.monotonic() - t0) * 1000)
    return SmokeResult(name=name, status="ok", duration_ms=dur)


class _Skip(Exception):
    """Internal sentinel — a subsystem entry point is intentionally
    absent in this build (e.g., feature gated out). Reported as ``skip``
    rather than ``fail``."""


# ---------------------------------------------------------------------------
# Subsystem boot probes
#
# Each probe is a zero-arg callable that raises if the subsystem cannot
# initialise. Probes MUST NOT make network calls and MUST NOT mutate
# persistent state. Probes that touch a contracts YAML / on-disk
# resource are the load-bearing ones — those are exactly the path-
# resolution bugs the smoke is meant to catch.
# ---------------------------------------------------------------------------


def _probe_media_integrity_factory() -> None:
    """v1.0.231 case study. ``build_default_service`` calls
    ``ServarrPolicy.load_default()`` which walks
    ``_CONTRACT_PATH_CANDIDATES`` for ``servarr-policy.yaml``. If the
    image lays the contract down at a path not in that list, the
    factory raises and the whole subsystem silently disables."""
    mod = importlib.import_module("media_stack.services.media_integrity.factory")
    # Use the real lookup paths but neutered http_client so adapter
    # construction stays in-process. ``env`` returns "" for everything,
    # so no adapters are wired — the call exercises the contract loader
    # + zero-adapter degenerate path, which is the path the v1.0.231
    # bug was on.
    svc = mod.build_default_service(env=lambda _k: "")
    if svc is None:
        raise RuntimeError("build_default_service returned None")


def _probe_guardrails_default() -> None:
    """``application.guardrails.registry.default()`` is the singleton
    every guardrails callsite goes through. Importing
    ``services.guardrails`` triggers domain side-effect registration —
    if any rule module raises during its top-level imports, the smoke
    sees it here. Bug class: a guardrail rule module with a YAML
    dependency that resolves differently inside the image."""
    importlib.import_module("media_stack.services.guardrails")
    reg_mod = importlib.import_module("media_stack.application.guardrails.registry")
    reg = reg_mod.default()
    if reg is None:
        raise RuntimeError("guardrails default() returned None")


def _probe_jobs_framework_jobrunner() -> None:
    """``JobRunner`` itself is pure orchestration, but instantiating it
    forces the framework module to import — that module has the
    ``_find_contracts_dir`` candidate list which is the same bug class
    as media-integrity. Constructing a no-op ``Job`` + ``JobContext``
    keeps the probe self-contained."""
    fw = importlib.import_module("media_stack.services.jobs.framework")
    job = fw.Job(name="image-smoke-noop", handler=lambda _ctx: {"ok": True})
    ctx = fw.JobContext()
    runner = fw.JobRunner(root=job, ctx=ctx)
    if runner is None:
        raise RuntimeError("JobRunner instantiation returned None")


def _probe_security_api_token_aggregator() -> None:
    """The aggregator's ``__init__`` wires up provider protocols and
    reads no disk; what we're really testing is that the module imports
    (it pulls in ``core.auth.users.visibility_protocols`` which has its
    own contract surface). Bug class: a protocol-renaming cleanup that
    breaks the import chain only when the wheel is laid down."""
    mod = importlib.import_module(
        "media_stack.services.security.api_token_aggregator"
    )
    agg = mod.APITokenAggregator()
    if agg is None:
        raise RuntimeError("APITokenAggregator() returned None")


def _probe_security_session_aggregator() -> None:
    """Same shape as the api-token aggregator: the import is the load-
    bearing part. ``SessionAggregator`` requires a session_store, so we
    pass a tiny stub that satisfies ``SessionStoreProtocol`` — we're
    testing the boot path, not the runtime logic."""
    mod = importlib.import_module(
        "media_stack.services.security.session_aggregator"
    )

    class _StubStore:
        def list_all_active(self) -> list:  # pragma: no cover — smoke stub
            return []

        def list_for(self, username: str) -> list:  # pragma: no cover — smoke stub
            return []

    agg = mod.SessionAggregator(session_store=_StubStore())
    if agg is None:
        raise RuntimeError("SessionAggregator(...) returned None")


def _probe_media_integrity_handlers_instance() -> None:
    """The handler module exposes ``_instance`` constructed at import
    time. Importing this module is what primes the
    ``MediaIntegrityHandlers`` singleton; if the import chain is broken
    (a typical symptom of a contracts path that doesn't exist), the
    operator sees "media-integrity service not configured" forever
    because the wiring step at controller-serve time never happens.
    The handler is allowed to be in the not-configured state — what
    must NOT happen is a crash on import."""
    mod = importlib.import_module(
        "media_stack.api.services.media_integrity_handlers"
    )
    inst = getattr(mod, "_instance", None)
    if inst is None:
        raise RuntimeError("_instance is None after import")
    # Verify the public method ``set_service`` is reachable; this is
    # the wire-up surface controller-serve uses.
    if not callable(getattr(inst, "set_service", None)):
        raise RuntimeError("_instance.set_service is not callable")


def _probe_disk_guardrails_service() -> None:
    """``DiskGuardrailsService`` is a dataclass with required callable
    deps; instantiating it with no-op stubs verifies the dataclass +
    its imports (``apps.download_clients.registry_helpers``) all
    resolve under the wheel layout."""
    mod = importlib.import_module("media_stack.services.disk_guardrails_service")
    noop = lambda *_a, **_kw: None  # noqa: E731
    svc = mod.DiskGuardrailsService(
        log=lambda _m: None,
        bool_cfg=lambda _c, _k, d: bool(d),
        coerce_list=lambda v: list(v) if isinstance(v, list) else [],
        to_int=lambda v, d: int(v) if isinstance(v, int) else d,
        to_float=lambda v, d: float(v) if isinstance(v, (int, float)) else d,
        normalize_url=lambda u: u,
        disk_usage_percent=lambda _p: (0.0, 0, 0),
        fmt_bytes=lambda _b: "0B",
        qbit_login=noop,
        qbit_list_completed_torrents=noop,
        qbit_delete_torrents=noop,
    )
    if svc is None:
        raise RuntimeError("DiskGuardrailsService(...) returned None")


def _probe_controller_service() -> None:
    """``ControllerService`` brings in the runner-operations registry
    + adapter-hooks chain at import. The dataclass takes a
    ``ControllerDependencies`` blob; we pass minimal no-op stubs so
    this is purely about the import + dataclass shape."""
    mod = importlib.import_module("media_stack.services.controller_service")
    runner_ops = importlib.import_module(
        "media_stack.services.runner_operations_service"
    )
    deps = mod.ControllerDependencies(
        log=lambda _m: None,
        bool_cfg=lambda _c, _k, d: bool(d),
        normalize_url=lambda u: u,
        wait_for_service=lambda *_a, **_kw: None,
        operations=runner_ops.RunnerOperationRegistry(),
    )
    svc = mod.ControllerService(deps=deps)
    if svc is None:
        raise RuntimeError("ControllerService(...) returned None")


def _probe_health_service() -> None:
    """``HealthService`` is a thin dataclass over an HTTP-request
    callable. Catches a regression in its module's import chain."""
    mod = importlib.import_module("media_stack.services.health_service")
    svc = mod.HealthService(
        http_request=lambda *_a, **_kw: (200, [], ""),
        log=lambda _m: None,
    )
    if svc is None:
        raise RuntimeError("HealthService(...) returned None")


def _probe_epg_provider_service() -> None:
    """``EpgProviderService`` walks ``_find_providers_yaml`` candidates
    on first ``_load_providers`` call. Missing candidates cause the
    service to silently return ``{}`` rather than raise — which is the
    same bug class as media-integrity but more subtle. We exercise the
    load to surface YAML-parsing issues here at build time."""
    mod = importlib.import_module("media_stack.services.epg_provider_service")
    svc = mod.EpgProviderService()
    # Trigger the YAML walk; absent file is OK (returns {}), parse
    # error is NOT.
    svc._load_providers()


def _probe_livetv_config_service() -> None:
    """``LiveTvConfigEnrichmentService`` itself is stateless; the smoke
    catches regressions in its module's import chain (which pulls in
    ``epg_provider_service`` + ``runtime_platform``)."""
    mod = importlib.import_module("media_stack.services.livetv_config_service")
    svc = mod.LiveTvConfigEnrichmentService()
    if svc is None:
        raise RuntimeError("LiveTvConfigEnrichmentService() returned None")


def _probe_adapter_factory() -> None:
    """The factory class itself, not a built adapter. Importing the
    module verifies its dependencies (``importlib`` + ``inspect`` only,
    so this is mostly a tripwire for someone re-routing a sibling
    import through it)."""
    mod = importlib.import_module("media_stack.services.adapter_factory")
    factory_cls = getattr(mod, "AdapterFactory", None)
    if factory_cls is None:
        raise RuntimeError("AdapterFactory class missing from module")
    # Sanity-call a static method with bad input to confirm the class
    # is wired (raises ValueError — that's expected and means the code
    # is reachable).
    try:
        factory_cls.load_adapter_class("", role="smoke")
    except ValueError:
        return
    raise RuntimeError(
        "AdapterFactory.load_adapter_class('') should have raised ValueError"
    )


def _probe_config_routing() -> None:
    """``api.services.config.get_routing`` is the canonical "read the
    profile YAML" call. If the profile path resolver lost a candidate,
    this returns the env-default routing dict but doesn't crash —
    which is the desired behavior. We assert non-None to catch a
    regression where the function starts raising on a missing file
    instead of degrading."""
    mod = importlib.import_module("media_stack.api.services.config")
    fn = getattr(mod, "get_routing", None)
    if fn is None:
        raise _Skip("api.services.config.get_routing not present")
    routing = fn()
    if routing is None:
        raise RuntimeError("get_routing() returned None")


# Ordered list. Keep names stable — the ratchet test counts entries.
_SUBSYSTEMS: list[tuple[str, Callable[[], None], str]] = [
    (
        "media_integrity.factory.build_default_service",
        _probe_media_integrity_factory,
        "v1.0.231: contracts path candidate missing → silent disable.",
    ),
    (
        "guardrails.default",
        _probe_guardrails_default,
        "Domain rule module imports — any one import failure breaks the lot.",
    ),
    (
        "jobs.framework.JobRunner",
        _probe_jobs_framework_jobrunner,
        "_find_contracts_dir uses the same path-candidate pattern.",
    ),
    (
        "security.api_token_aggregator",
        _probe_security_api_token_aggregator,
        "Provider-protocol import chain — image-only renames lurk here.",
    ),
    (
        "security.session_aggregator",
        _probe_security_session_aggregator,
        "Same shape as api_token_aggregator; protect via the same probe.",
    ),
    (
        "api.services.media_integrity_handlers._instance",
        _probe_media_integrity_handlers_instance,
        "Module-level singleton; import failure = handler perma-unconfigured.",
    ),
    (
        "services.disk_guardrails_service",
        _probe_disk_guardrails_service,
        "Imports apps/download_clients/registry_helpers — image-laid contract.",
    ),
    (
        "services.controller_service",
        _probe_controller_service,
        "Pulls in runner-operations + adapter-hooks; deep import surface.",
    ),
    (
        "services.health_service",
        _probe_health_service,
        "Thin but easy regression target — import-chain tripwire.",
    ),
    (
        "services.epg_provider_service",
        _probe_epg_provider_service,
        "_find_providers_yaml: same path-candidate bug class.",
    ),
    (
        "services.livetv_config_service",
        _probe_livetv_config_service,
        "Imports epg_provider_service + runtime_platform.",
    ),
    (
        "services.adapter_factory",
        _probe_adapter_factory,
        "Spec-string adapter loader; tripwire for the import surface.",
    ),
    (
        "api.services.config.get_routing",
        _probe_config_routing,
        "Profile YAML resolver — path candidate regression class.",
    ),
]


def main() -> int:
    """Entry point — run every probe, print the table, return exit code.

    Exit code 0 on all-pass (skipped is allowed, failed is not).
    Exit code 1 on any failure.
    """
    started = time.monotonic()
    results: list[SmokeResult] = []
    for name, fn, why in _SUBSYSTEMS:
        results.append(_run(name, fn, why=why))

    # Build the table. Columns: name | status | ms.
    name_w = max(len("subsystem"), max(len(r.name) for r in results))
    print()
    print(f"{'subsystem'.ljust(name_w)} | status | duration_ms")
    print("-" * (name_w + 25))
    for r in results:
        print(f"{r.name.ljust(name_w)} | {r.status.ljust(6)} | {r.duration_ms}")

    failures = [r for r in results if r.status == "fail"]
    if failures:
        print()
        print(f"FAIL: {len(failures)} subsystem(s) did not boot cleanly:")
        for r in failures:
            print()
            print(f"=== {r.name} ===")
            print(r.detail)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    print()
    print(
        f"image-smoke: {len(results)} subsystems, "
        f"{sum(1 for r in results if r.status == 'ok')} ok, "
        f"{sum(1 for r in results if r.status == 'skip')} skipped, "
        f"{len(failures)} failed in {elapsed_ms} ms"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
