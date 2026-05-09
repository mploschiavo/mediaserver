"""Resource-aware unittest discovery and execution helpers."""

from __future__ import annotations

import resource
import signal
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class UnitTestRunnerConfig:
    root_dir: Path
    start_dir: str = "tests/unit"
    pattern: str = "test_*.py"
    top_n: int = 10
    verbosity: int = 1
    failfast: bool = False
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class UnitTestTelemetryRecord:
    test_id: str
    status: str
    elapsed_seconds: float
    rss_start_kib: int | None
    rss_end_kib: int | None
    rss_delta_kib: int | None
    peak_rss_kib: int | None
    peak_rss_delta_kib: int | None


class UnitTestTimeoutError(TimeoutError):
    """Raised when a unit test exceeds the configured timeout budget."""


class UnitTestRunnerService:
    """Resource-aware unittest discovery, execution, and telemetry summary."""

    def read_proc_rss_kib(self) -> int | None:
        try:
            with open("/proc/self/status", encoding="utf-8") as handle:
                for line in handle:
                    if not line.startswith("VmRSS:"):
                        continue
                    parts = line.split()
                    if len(parts) < 2:
                        return None
                    return int(parts[1])
        except (FileNotFoundError, OSError, ValueError):
            return None
        return None

    def read_peak_rss_kib(self) -> int | None:
        try:
            value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        except (AttributeError, ValueError):
            return None
        # macOS reports bytes, Linux reports kibibytes.
        if value > 0 and value > 1024 * 1024 * 8:
            return value // 1024
        return value

    def format_mib(self, value_kib: int | None) -> str:
        if value_kib is None:
            return "n/a"
        return f"{value_kib / 1024:.1f}"

    def memory_key(self, record: UnitTestTelemetryRecord) -> tuple[int, int]:
        peak_delta = record.peak_rss_delta_kib if record.peak_rss_delta_kib is not None else -1
        rss_delta = record.rss_delta_kib if record.rss_delta_kib is not None else -1
        return peak_delta, rss_delta

    def write_offenders_summary(
        self,
        stream: TextIO,
        records: list[UnitTestTelemetryRecord],
        top_n: int,
    ) -> None:
        if not records:
            return
        limit = max(1, top_n)
        stream.write("\n")
        stream.write(
            "[TEST-RESOURCE] summary "
            f"total_tests={len(records)} "
            f"top_n={min(limit, len(records))}\n"
        )

        slowest = sorted(records, key=lambda item: item.elapsed_seconds, reverse=True)[:limit]
        stream.write("[TEST-RESOURCE] slowest_tests\n")
        for item in slowest:
            stream.write(
                "[TEST-RESOURCE][slow] "
                f"elapsed_s={item.elapsed_seconds:.3f} "
                f"status={item.status} "
                f"test={item.test_id}\n"
            )

        memory_heaviest = sorted(records, key=self.memory_key, reverse=True)[:limit]
        stream.write("[TEST-RESOURCE] highest_memory_tests\n")
        for item in memory_heaviest:
            stream.write(
                "[TEST-RESOURCE][memory] "
                f"peak_delta_mib={self.format_mib(item.peak_rss_delta_kib)} "
                f"rss_delta_mib={self.format_mib(item.rss_delta_kib)} "
                f"status={item.status} "
                f"test={item.test_id}\n"
            )

    def discover_unit_test_suite(self, cfg: UnitTestRunnerConfig) -> unittest.TestSuite:
        discover_root = cfg.root_dir / cfg.start_dir
        return unittest.defaultTestLoader.discover(
            start_dir=str(discover_root), pattern=cfg.pattern
        )

    def run_unit_test_suite(
        self,
        suite: unittest.TestSuite,
        cfg: UnitTestRunnerConfig,
        stream: TextIO,
    ) -> tuple[int, list[UnitTestTelemetryRecord]]:
        runner = ResourceTelemetryTextTestRunner(
            stream=stream,
            verbosity=cfg.verbosity,
            failfast=cfg.failfast,
            timeout_seconds=cfg.timeout_seconds,
        )
        result = runner.run(suite)
        telemetry_records: list[UnitTestTelemetryRecord] = list(result.telemetry_records)
        self.write_offenders_summary(stream=stream, records=telemetry_records, top_n=cfg.top_n)
        return (0 if result.wasSuccessful() else 1, telemetry_records)

    def run_discovered_unit_tests(
        self,
        cfg: UnitTestRunnerConfig,
        stream: TextIO,
    ) -> tuple[int, list[UnitTestTelemetryRecord]]:
        suite = self.discover_unit_test_suite(cfg)
        return self.run_unit_test_suite(suite=suite, cfg=cfg, stream=stream)


_SERVICE = UnitTestRunnerService()


class ResourceTelemetryTestResult(unittest.TextTestResult):
    def __init__(
        self,
        stream,
        descriptions: bool,
        verbosity: int,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(stream, descriptions, verbosity)
        self.telemetry_records: list[UnitTestTelemetryRecord] = []
        self._status_by_test_id: dict[str, str] = {}
        self._start_time_by_test_id: dict[str, float] = {}
        self._rss_start_by_test_id: dict[str, int | None] = {}
        self._peak_start_by_test_id: dict[str, int | None] = {}
        self._timeout_seconds = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        self._active_test_id: str | None = None
        self._signal_restore_by_test_id: dict[str, object] = {}
        self._itimer_restore_by_test_id: dict[str, tuple[float, float] | None] = {}

    def _timeouts_supported(self) -> bool:
        if self._timeout_seconds is None:
            return False
        if threading.current_thread() is not threading.main_thread():
            return False
        return hasattr(signal, "SIGALRM")

    def _handle_timeout(self, signum, frame) -> None:
        del signum, frame
        if self._active_test_id is not None:
            self._status_by_test_id[self._active_test_id] = "timeout"
        raise UnitTestTimeoutError(
            f"Test exceeded timeout budget ({self._timeout_seconds:.3f} seconds)."
        )

    def _arm_timeout(self, test_id: str) -> None:
        if not self._timeouts_supported():
            return
        self._signal_restore_by_test_id[test_id] = signal.getsignal(signal.SIGALRM)
        if hasattr(signal, "setitimer") and hasattr(signal, "ITIMER_REAL"):
            previous = signal.getitimer(signal.ITIMER_REAL)
            self._itimer_restore_by_test_id[test_id] = previous
            signal.signal(signal.SIGALRM, self._handle_timeout)
            signal.setitimer(signal.ITIMER_REAL, float(self._timeout_seconds))
            return
        self._itimer_restore_by_test_id[test_id] = None
        signal.signal(signal.SIGALRM, self._handle_timeout)
        alarm_seconds = max(1, int(float(self._timeout_seconds)))
        signal.alarm(alarm_seconds)

    def _disarm_timeout(self, test_id: str) -> None:
        if not self._timeouts_supported():
            return
        if hasattr(signal, "setitimer") and hasattr(signal, "ITIMER_REAL"):
            signal.setitimer(signal.ITIMER_REAL, 0)
            previous_timer = self._itimer_restore_by_test_id.pop(test_id, None)
            if previous_timer is not None:
                signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])
        else:
            signal.alarm(0)
            self._itimer_restore_by_test_id.pop(test_id, None)
        previous_handler = self._signal_restore_by_test_id.pop(test_id, None)
        if previous_handler is not None:
            signal.signal(signal.SIGALRM, previous_handler)

    def _set_status(self, test: unittest.TestCase, status: str) -> None:
        self._status_by_test_id[test.id()] = status

    def startTest(self, test: unittest.TestCase) -> None:  # noqa: N802 (stdlib override)
        super().startTest(test)
        test_id = test.id()
        self._status_by_test_id[test_id] = "ok"
        self._start_time_by_test_id[test_id] = time.perf_counter()
        self._rss_start_by_test_id[test_id] = _SERVICE.read_proc_rss_kib()
        self._peak_start_by_test_id[test_id] = _SERVICE.read_peak_rss_kib()
        self._active_test_id = test_id
        self._arm_timeout(test_id)

    def stopTest(self, test: unittest.TestCase) -> None:  # noqa: N802 (stdlib override)
        test_id = test.id()
        self._disarm_timeout(test_id)
        if self._active_test_id == test_id:
            self._active_test_id = None
        elapsed_seconds = time.perf_counter() - self._start_time_by_test_id.pop(
            test_id, time.perf_counter()
        )
        rss_start_kib = self._rss_start_by_test_id.pop(test_id, None)
        rss_end_kib = _SERVICE.read_proc_rss_kib()
        rss_delta_kib = (
            None
            if rss_start_kib is None or rss_end_kib is None
            else int(rss_end_kib - rss_start_kib)
        )
        peak_start_kib = self._peak_start_by_test_id.pop(test_id, None)
        peak_end_kib = _SERVICE.read_peak_rss_kib()
        peak_rss_delta_kib = (
            None
            if peak_start_kib is None or peak_end_kib is None
            else int(peak_end_kib - peak_start_kib)
        )
        status = self._status_by_test_id.pop(test_id, "ok")
        record = UnitTestTelemetryRecord(
            test_id=test_id,
            status=status,
            elapsed_seconds=elapsed_seconds,
            rss_start_kib=rss_start_kib,
            rss_end_kib=rss_end_kib,
            rss_delta_kib=rss_delta_kib,
            peak_rss_kib=peak_end_kib,
            peak_rss_delta_kib=peak_rss_delta_kib,
        )
        self.telemetry_records.append(record)
        self.stream.writeln(
            "[TEST-RESOURCE] "
            f"status={record.status} "
            f"test={record.test_id} "
            f"elapsed_s={record.elapsed_seconds:.3f} "
            f"rss_delta_mib={_SERVICE.format_mib(record.rss_delta_kib)} "
            f"peak_delta_mib={_SERVICE.format_mib(record.peak_rss_delta_kib)} "
            f"peak_mib={_SERVICE.format_mib(record.peak_rss_kib)}"
        )
        super().stopTest(test)

    def addSuccess(self, test: unittest.TestCase) -> None:  # noqa: N802 (stdlib override)
        self._set_status(test, "ok")
        super().addSuccess(test)

    def addError(self, test: unittest.TestCase, err) -> None:  # noqa: N802 (stdlib override)
        exc_type = err[0] if isinstance(err, tuple) and len(err) >= 1 else None
        if isinstance(exc_type, type) and issubclass(exc_type, UnitTestTimeoutError):
            self._set_status(test, "timeout")
        else:
            self._set_status(test, "error")
        super().addError(test, err)

    def addFailure(self, test: unittest.TestCase, err) -> None:  # noqa: N802 (stdlib override)
        self._set_status(test, "fail")
        super().addFailure(test, err)

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:  # noqa: N802 (stdlib override)
        self._set_status(test, "skip")
        super().addSkip(test, reason)

    def addExpectedFailure(self, test: unittest.TestCase, err) -> None:  # noqa: N802
        self._set_status(test, "expected-fail")
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:  # noqa: N802
        self._set_status(test, "unexpected-success")
        super().addUnexpectedSuccess(test)


class ResourceTelemetryTextTestRunner(unittest.TextTestRunner):
    resultclass = ResourceTelemetryTestResult

    def __init__(self, *args, timeout_seconds: float | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._timeout_seconds = timeout_seconds

    def _makeResult(self):
        return self.resultclass(
            self.stream,
            self.descriptions,
            self.verbosity,
            timeout_seconds=self._timeout_seconds,
        )


# Module-level aliases preserving the public import API for callers + tests.
_read_proc_rss_kib = _SERVICE.read_proc_rss_kib
_read_peak_rss_kib = _SERVICE.read_peak_rss_kib
_format_mib = _SERVICE.format_mib
_memory_key = _SERVICE.memory_key
_write_offenders_summary = _SERVICE.write_offenders_summary
discover_unit_test_suite = _SERVICE.discover_unit_test_suite
run_unit_test_suite = _SERVICE.run_unit_test_suite
run_discovered_unit_tests = _SERVICE.run_discovered_unit_tests
