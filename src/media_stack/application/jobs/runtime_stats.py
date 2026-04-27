"""Per-job rolling runtime statistics with online mean/stdev.

Tracks the last ``WINDOW`` durations for every job_name and exposes
a z-score the UI uses to tint anomalously-slow runs in red. The
classic Welford recurrence keeps mean + variance in O(1) per
sample without needing the full window in memory; the ring buffer
is kept anyway because the UI's chart band overlay (deferred to a
follow-up) needs the raw points.

Confidence gate: the first ``MIN_SAMPLES`` runs of a new job
return ``None`` for the z-score. Tinting on a single-sample
distribution would mark literally every run as anomalous because
stdev is undefined; the gate keeps the UI honest until enough
history has accumulated for the comparison to mean something.

Memory cost: one ``JobStats`` per distinct job_name. Each carries
two ints, two floats, and a deque of at most ``WINDOW`` floats
(~3.2 KB per job at WINDOW=100). With ~30 contract-discovered
jobs that's <100 KB — well below any concerning controller-process
budget.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, field

# Last N runs per job. Larger windows give stabler stats but slower
# response to a regime change (e.g. a job that just got a 10x
# speedup will keep flagging fast runs as "anomalous below" until
# the slow ones rotate out). 100 balances both for our cadence.
WINDOW = 100

# Below this many samples, return ``None`` for the z-score —
# stdev is too noisy to mean anything before then.
MIN_SAMPLES = 10


@dataclass
class JobStats:
    """Online running mean / variance for a single job_name.

    Welford keeps:

      n     — sample count
      mean  — running mean
      m2    — sum of squared deltas from the running mean

    From which ``stdev = sqrt(m2 / (n - 1))`` for n>1.
    """

    n: int = 0
    mean: float = 0.0
    m2: float = 0.0
    durations: deque[float] = field(
        default_factory=lambda: deque(maxlen=WINDOW),
    )

    def add(self, value: float) -> None:
        """Incorporate a new duration sample into the running stats.

        Welford's recurrence:

            n   <- n + 1
            d   <- value - mean
            mean <- mean + d / n
            m2  <- m2 + d * (value - mean)   # updated mean!
        """
        self.n += 1
        delta = value - self.mean
        self.mean += delta / self.n
        delta2 = value - self.mean
        self.m2 += delta * delta2
        self.durations.append(value)

    @property
    def stdev(self) -> float:
        """Sample standard deviation; ``0`` for n<2.

        We use the n-1 (Bessel-corrected) form so the metric is
        unbiased on small windows. The 0-fallback for n<2 matches
        the contract that ``z_score`` returns ``None`` below
        MIN_SAMPLES — a 0 stdev never reaches the z-score path.
        """
        if self.n < 2:
            return 0.0
        return math.sqrt(self.m2 / (self.n - 1))

    def z_score(self, value: float) -> float | None:
        """Number of standard deviations ``value`` sits above the
        running mean. Returns ``None`` when there's not enough
        history (n<MIN_SAMPLES) or stdev is 0 (degenerate case
        where every sample equals the mean — the recurring job
        with a fixed-step elapsed)."""
        if self.n < MIN_SAMPLES:
            return None
        sd = self.stdev
        if sd == 0:
            return None
        return (value - self.mean) / sd


class JobRuntimeStats:
    """Process-wide registry of per-job rolling stats.

    Thread-safe via a single coarse-grained lock. The hot path is
    one entry per ``record_run_complete`` call (a few microseconds
    per add); cross-job concurrency isn't worth a finer-grained
    scheme for that volume.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_job: dict[str, JobStats] = {}

    def add(self, job_name: str, duration: float) -> float | None:
        """Record a new completed-run duration for ``job_name``.

        Returns the z-score *before* this sample folded in (i.e.
        "how anomalous was this run vs the prior baseline?"). The
        baseline-prior interpretation is intentional: scoring
        post-fold would always score the very-recent value vs a
        mean it just shifted, which dilutes the signal.
        """
        with self._lock:
            stats = self._by_job.get(job_name)
            if stats is None:
                stats = JobStats()
                self._by_job[job_name] = stats
            score = stats.z_score(duration)
            stats.add(duration)
            return score

    def stats_for(self, job_name: str) -> JobStats | None:
        """Read-only snapshot for the chart endpoint. Returns
        ``None`` for jobs that have never recorded a run."""
        with self._lock:
            return self._by_job.get(job_name)

    def reset(self) -> None:
        """Drop every job's stats — for test isolation only.

        Production never resets; the buffer rebuilds naturally as
        runs land. Tests that expect a clean slate per case call
        this in a fixture teardown.
        """
        with self._lock:
            self._by_job.clear()


_default = JobRuntimeStats()


def add_run(job_name: str, duration: float) -> float | None:
    """Module-level shortcut to ``_default.add`` so free functions
    in ``run_history`` don't have to thread an instance reference."""
    return _default.add(job_name, duration)


def stats_for(job_name: str) -> JobStats | None:
    return _default.stats_for(job_name)


def reset_default() -> None:
    _default.reset()
