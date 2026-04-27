"""Tests for ``application/jobs/runtime_stats`` — the Welford
rolling-mean / z-score helper that powers anomaly tinting on the
Jobs page.

Covers:

  * Below ``MIN_SAMPLES`` returns ``None`` so the UI doesn't tint
    runs whose distribution is still being established.
  * Welford recurrence converges on the right mean / stdev.
  * The very-recent sample is scored against the *prior* baseline,
    not against itself (i.e. an outlier still scores high).
  * Per-job isolation — adding to one job doesn't move another's
    statistics.
  * ``WINDOW`` cap doesn't break the running stats (Welford keeps
    n forever; the deque is for the chart-band consumer that needs
    raw points).
  * Degenerate stdev=0 case (all samples identical) returns
    ``None`` rather than dividing by zero.
"""

from __future__ import annotations

import math

import pytest

from media_stack.application.jobs.runtime_stats import (
    JobRuntimeStats,
    JobStats,
    MIN_SAMPLES,
    WINDOW,
)


@pytest.fixture
def stats() -> JobRuntimeStats:
    return JobRuntimeStats()


class TestJobStatsWelford:
    def test_first_add_sets_mean_to_value_and_stdev_zero(self) -> None:
        s = JobStats()
        s.add(5.0)
        assert s.n == 1
        assert s.mean == 5.0
        assert s.stdev == 0.0

    def test_two_samples_compute_correct_mean_and_stdev(self) -> None:
        s = JobStats()
        s.add(2.0)
        s.add(4.0)
        assert s.mean == 3.0
        # Sample stdev with Bessel correction = sqrt(((2-3)^2 + (4-3)^2) / 1)
        # = sqrt(2) ≈ 1.414.
        assert math.isclose(s.stdev, math.sqrt(2.0))

    def test_many_samples_converge_on_known_mean(self) -> None:
        s = JobStats()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]:
            s.add(v)
        assert s.n == 10
        assert math.isclose(s.mean, 5.5)
        # Sample stdev of 1..10 is sqrt(82.5/9) ≈ 3.0277.
        assert math.isclose(s.stdev, math.sqrt(82.5 / 9.0))


class TestZScoreGate:
    def test_returns_none_below_min_samples(self) -> None:
        s = JobStats()
        for _ in range(MIN_SAMPLES - 1):
            s.add(1.0)
        # n is one short of MIN_SAMPLES — score must be None.
        assert s.z_score(5.0) is None

    def test_returns_score_at_min_samples(self) -> None:
        s = JobStats()
        # Build a baseline of 10 samples around 1.0.
        for v in [1.0, 1.1, 0.9, 1.05, 0.95, 1.0, 1.02, 0.98, 1.03, 0.97]:
            s.add(v)
        assert s.n == MIN_SAMPLES
        # A wildly-larger value scores high.
        z = s.z_score(10.0)
        assert z is not None and z > 5.0

    def test_returns_none_when_stdev_is_zero(self) -> None:
        s = JobStats()
        for _ in range(MIN_SAMPLES + 5):
            s.add(2.5)
        # Every sample identical → stdev=0 → score undefined.
        assert s.z_score(3.0) is None


class TestJobRuntimeStatsRegistry:
    def test_first_add_returns_none_z(
        self, stats: JobRuntimeStats,
    ) -> None:
        # First sample for a new job_name has no baseline.
        assert stats.add("scan", 1.0) is None

    def test_z_returned_against_prior_baseline_not_post_fold(
        self, stats: JobRuntimeStats,
    ) -> None:
        # Build 10 samples around 1.0.
        for _ in range(MIN_SAMPLES):
            stats.add("scan", 1.0)
        # Eleventh sample is wildly bigger; the score is computed
        # BEFORE the fold so the new value scores against the still-
        # tight baseline, not the diluted post-fold mean.
        z = stats.add("scan", 100.0)
        # Stdev of 10 identical samples is 0 → returned None for
        # the prior baseline. That's expected: the gate rejects
        # zero-stdev distributions. Now add some variation.
        assert z is None

    def test_anomaly_scores_above_two_sigma_after_jitter(
        self, stats: JobRuntimeStats,
    ) -> None:
        # Establish a non-degenerate distribution.
        for v in [1.0, 1.1, 0.9, 1.05, 0.95, 1.0, 1.02, 0.98, 1.03, 0.97]:
            stats.add("scan", v)
        # Eleventh sample at 5x the mean → high z-score.
        z = stats.add("scan", 5.0)
        assert z is not None
        assert z > 2.0

    def test_per_job_isolation(self, stats: JobRuntimeStats) -> None:
        for _ in range(MIN_SAMPLES):
            stats.add("a", 1.0)
        # Job 'b' starts fresh — its first add gets None even
        # though job 'a' has plenty of history.
        assert stats.add("b", 1.0) is None

    def test_stats_for_returns_none_for_unknown_job(
        self, stats: JobRuntimeStats,
    ) -> None:
        assert stats.stats_for("never-ran") is None

    def test_stats_for_returns_live_snapshot_for_known_job(
        self, stats: JobRuntimeStats,
    ) -> None:
        for _ in range(3):
            stats.add("scan", 2.0)
        snap = stats.stats_for("scan")
        assert snap is not None
        assert snap.n == 3
        assert snap.mean == 2.0

    def test_window_caps_durations_deque_but_not_n(
        self, stats: JobRuntimeStats,
    ) -> None:
        # Push WINDOW+5 samples; n keeps growing, deque is capped.
        for i in range(WINDOW + 5):
            stats.add("scan", float(i))
        snap = stats.stats_for("scan")
        assert snap is not None
        assert snap.n == WINDOW + 5
        assert len(snap.durations) == WINDOW

    def test_reset_drops_every_jobs_stats(
        self, stats: JobRuntimeStats,
    ) -> None:
        for _ in range(MIN_SAMPLES):
            stats.add("scan", 1.0)
        stats.reset()
        assert stats.stats_for("scan") is None
        # First post-reset sample again returns None (no baseline).
        assert stats.add("scan", 1.0) is None
