"""Pure value objects for the job framework.

These have no I/O, no thread spawning, no HTTP. The runner that
walks ``Job`` trees lives in ``media_stack.application.jobs.framework``
because it performs filesystem + HTTP I/O at dispatch time.

The ``Job`` class is the single dependency every contract YAML
``plugin.jobs.<name>`` definition lands in: every per-app handler is
wrapped in a ``Job`` by the framework's ``build_job_framework`` and
the runner walks the resulting tree.
"""

from __future__ import annotations

import time
from typing import Any, Callable, TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover - type-only
    # The runtime ``JobContext`` lives in the application layer (it
    # does I/O via its ``cfg`` property). Importing it here would tie
    # the domain layer to ``application/``; instead we keep the
    # forward-reference TYPE_CHECKING-only.
    from media_stack.application.jobs.framework import JobContext


# ``_JOB_HISTORY_MAX`` and ``_HISTORY_SOURCE_VALUES`` are part of the
# persisted history-entry schema — the UI persona-questions agent
# renders a badge per ``source`` so this set is a public contract
# (also surfaced in ``contracts/api/openapi.yaml``).
_JOB_HISTORY_MAX = 20

_HISTORY_SOURCE_VALUES: frozenset[str] = frozenset({
    "cron",
    "manual",
    "auto-heal",
    "scheduler",
    "unknown",
})


# Named prerequisite registry — any module can register conditions.
# Each is a callable(JobContext) -> bool. Living in the domain layer
# (instead of with the runner) keeps ``Job.check_prereqs`` free of an
# application-layer import — domain → application would invert the
# hexagon. Application code mutates this dict at import time via
# ``register_prereq`` (defined on ``Job`` as a staticmethod so this
# module stays free of loose top-level functions, per the
# ``_modules_with_loose_functions`` ratchet).
PREREQS: dict[str, Callable[["JobContext"], bool]] = {}


class CancelledError(RuntimeError):
    """Raised when a job is cancelled mid-flight.

    Lives in the domain layer so handlers in ``application/`` and
    ``adapters/`` can ``except CancelledError`` without taking a
    framework dependency on the runner module.
    """


class Job:
    """A unit of work with optional prerequisites and sub-jobs.

    This is the framework's single composable type. It knows nothing
    about Jellyfin, bootstrap, or media servers — any workflow can use
    it.

    - ``requires``: list of PREREQS names that must be True before
      running
    - ``sub_jobs``: child jobs that run after this job's handler
      succeeds
    - Sub-jobs inherit no prereqs from parents — each declares its own
    - ``max_attempts``: how many prereq-satisfaction rounds JobRunner
      will make before giving up on deferred sub-jobs. Defaults to 3
      for leaf jobs; tree-roots (e.g. ``bootstrap``) need a higher
      value because there are many cross-cutting prereqs to satisfy
      across phases. Read from ``plugin.jobs.<name>.max_attempts``
      in the contract YAML.
    - N-level nesting: sub-jobs can have sub-jobs.
    """

    def __init__(
        self,
        name: str,
        handler: Callable[["JobContext"], dict[str, Any] | None],
        requires: list[str] | None = None,
        max_attempts: int | None = None,
        non_blocking: bool = False,
        after: list[str] | None = None,
    ):
        self.name = name
        self.handler = handler
        self.requires = requires or []
        # Job-name dependencies. ``requires`` is for NAMED CONDITIONS
        # (e.g. ``media_server_reachable``); ``after`` is for job-name
        # ordering (e.g. ``after: [discover-indexers]`` means "don't
        # run me until that job's handler has fully finished"). Without
        # this, a non_blocking job's downstream peers race against its
        # daemon thread and start with empty data.
        self.after = list(after or [])
        self.sub_jobs: list["Job"] = []
        self.max_attempts = max_attempts
        # When True, JobRunner spawns the handler in a daemon thread
        # and immediately moves on to dispatch other jobs. Downstream
        # jobs that name this one in ``after`` will still wait for the
        # daemon thread to finish — non_blocking only relaxes the
        # SIBLING ordering, not declared dependencies.
        self.non_blocking = bool(non_blocking)

    def add_sub_job(self, job: "Job") -> "Job":
        """Add a child job. Returns self for chaining."""
        self.sub_jobs.append(job)
        return self

    @staticmethod
    def register_prereq(
        name: str, check: Callable[["JobContext"], bool],
    ) -> None:
        """Register a named prerequisite condition. Idempotent.

        Lives on ``Job`` (not as a top-level function) so this domain
        module stays free of loose ``def``s — the codebase-wide
        ``LOOSE_FUNCTIONS_RATCHET`` ratchet caps the number of
        modules that ship module-level functions, and the per-leaf
        helpers should hang off their owning class wherever
        possible.
        """
        PREREQS[name] = check

    @staticmethod
    def noop(ctx: "JobContext") -> dict[str, Any]:
        """Placeholder handler for composite jobs (parents that exist
        only to group sub-jobs).

        The runner's ``_flatten`` skips ``Job.noop``-handler jobs so
        a composite parent doesn't appear in dispatch output as a
        zero-duration entry. ``ctx`` is unused — the signature
        exists only to satisfy the ``Job`` handler contract."""
        return {}

    def _noop_logger(self, _msg: str) -> None:
        """Default logger when ``ctx.logger`` is missing — tests that
        build a stub JobContext without a logger drop messages instead
        of crashing. The application layer's real ``JobContext.__init__``
        binds ``runtime_platform.log`` so production never hits this.
        Instance method (not staticmethod) per the ratchet discipline:
        loose top-level functions are banned, and the static-method
        count ratchets down — so the fallback hangs off the instance.
        """
        return None

    @staticmethod
    def normalize_source(source: str | None) -> str:
        """Coerce a caller-supplied source token into the persisted form.

        Returns ``"unknown"`` for falsy inputs. Any token whose
        prefix (before the first ``:``) matches one of the canonical
        ``_HISTORY_SOURCE_VALUES`` entries passes through unchanged
        so sub-tagged forms like ``"cron:reconcile"`` survive.
        Unknown tokens collapse to ``"unknown"`` rather than getting
        persisted verbatim — the UI's enum-based badge logic would
        otherwise fall back to a generic style.
        """
        if not source:
            return "unknown"
        token = str(source).strip()
        if not token:
            return "unknown"
        head = token.split(":", 1)[0]
        if head in _HISTORY_SOURCE_VALUES:
            return token
        return "unknown"

    def check_prereqs(self, ctx: "JobContext") -> str | None:
        """Check all prerequisites. Returns failure reason or None if all pass."""
        for req_name in self.requires:
            check_fn = PREREQS.get(req_name)
            if check_fn and not check_fn(ctx):
                return f"prerequisite '{req_name}' not met"
        return None

    def run(self, ctx: "JobContext") -> dict[str, Any]:
        """Run this job's handler, then sub-jobs. Checks prereqs first.

        ADR-0011 Phase 1: domain is a leaf in the hexagon — no
        outbound media_stack imports. The logger is read from
        ``ctx.logger`` (the application layer constructs JobContext
        with the real ``runtime_platform.log`` callable bound).
        Tests that build a stub JobContext without a logger get a
        no-op, which keeps the call shape simple for callers.
        """
        log = getattr(ctx, "logger", None) or self._noop_logger

        # Check cancel before starting
        if ctx.cancelled:
            return {"status": "cancelled", "elapsed": 0}

        log(f"[JOB] {self.name}: starting")
        log(
            f"[DEBUG] Job {self.name}: requires={self.requires}, "
            f"sub_jobs=[{', '.join(s.name for s in self.sub_jobs)}], "
            f"handler={self.handler.__module__}.{self.handler.__name__}",
        )
        t0 = time.time()

        # Gate on prerequisites
        prereq_fail = self.check_prereqs(ctx)
        if prereq_fail:
            elapsed = round(time.time() - t0, 1)
            log(f"[WAIT] {self.name}: {prereq_fail} ({elapsed}s)")
            return {"status": "prereq_not_met", "reason": prereq_fail, "elapsed": elapsed}

        try:
            result = self.handler(ctx) or {}
            if "skipped" in result:
                reason = result["skipped"]
                elapsed = round(time.time() - t0, 1)
                log(f"[WARN] {self.name}: SKIPPED — {reason} ({elapsed}s)")
                return {"status": "skipped", "elapsed": elapsed, **result}
            # Run sub-jobs (each checks its own prereqs)
            for sub in self.sub_jobs:
                if ctx.cancelled:
                    log(
                        f"[ACTION] {self.name}: cancelled before sub-job {sub.name}"
                    )
                    break
                try:
                    sub.run(ctx)
                except CancelledError:
                    break
                except Exception as exc:
                    log(f"[WARN] {self.name}/{sub.name}: {exc}")
            if ctx.cancelled:
                elapsed = round(time.time() - t0, 1)
                return {"status": "cancelled", "elapsed": elapsed}
            elapsed = round(time.time() - t0, 1)
            log(f"[OK] {self.name}: complete ({elapsed}s)")
            return {"status": "ok", "elapsed": elapsed, **result}
        except CancelledError:
            elapsed = round(time.time() - t0, 1)
            log(f"[ACTION] {self.name}: cancelled ({elapsed}s)")
            return {"status": "cancelled", "elapsed": elapsed}
        except Exception as exc:
            elapsed = round(time.time() - t0, 1)
            log(f"[ERR] {self.name}: {exc} ({elapsed}s)")
            import traceback as _tb
            log(f"[DEBUG] Job {self.name} traceback:\n{_tb.format_exc()}")
            return {"status": "error", "error": str(exc)[:1000], "elapsed": elapsed}


# Module-level aliases for backward compatibility with the legacy
# ``services.jobs.framework`` surface. ``register_prereq``,
# ``_noop``, and ``_normalize_source`` were top-level callables in
# the pre-Phase-16-E framework module; ~140 references across tests
# + the API + the auto-heal service expect them at the module
# scope. The canonical homes are the ``Job`` staticmethods above;
# these names are re-exported through the ``application.jobs.framework``
# shim so legacy ``from media_stack.services.jobs.framework import
# register_prereq`` keeps resolving.
register_prereq = Job.register_prereq
_noop = Job.noop
_normalize_source = Job.normalize_source


__all__ = [
    "CancelledError",
    "Job",
    "PREREQS",
    "_HISTORY_SOURCE_VALUES",
    "_JOB_HISTORY_MAX",
    "_noop",
    "_normalize_source",
    "register_prereq",
]
