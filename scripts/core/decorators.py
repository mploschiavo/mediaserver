"""Cross-cutting decorators (retry/timing)."""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


RetryPredicate = Callable[[Exception], bool]


def timed(
    operation: str,
    *,
    logger: logging.Logger | None = None,
    level: int = logging.DEBUG,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Record execution time for a function call."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                log = logger or logging.getLogger("media_stack")
                log.log(level, "timing operation=%s duration_ms=%.2f", operation, elapsed_ms)

        return wrapper

    return decorator


def retry(
    *,
    attempts: int = 3,
    delay_seconds: float = 0.5,
    max_delay_seconds: float = 3.0,
    backoff_multiplier: float = 2.0,
    retry_if: RetryPredicate | None = None,
    logger: logging.Logger | None = None,
    operation: str = "operation",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Retry a function for transient failures."""

    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be >= 0")
    if max_delay_seconds < 0:
        raise ValueError("max_delay_seconds must be >= 0")
    if backoff_multiplier < 1:
        raise ValueError("backoff_multiplier must be >= 1")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            log = logger or logging.getLogger("media_stack")
            sleep_seconds = delay_seconds
            attempt = 1

            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    is_retryable = retry_if(exc) if retry_if is not None else True
                    should_retry = attempt < attempts and is_retryable
                    if not should_retry:
                        raise

                    log.warning(
                        "retry operation=%s attempt=%s/%s delay_seconds=%.2f error=%s",
                        operation,
                        attempt,
                        attempts,
                        sleep_seconds,
                        exc,
                    )
                    time.sleep(sleep_seconds)
                    attempt += 1
                    sleep_seconds = min(max_delay_seconds, sleep_seconds * backoff_multiplier)

        return wrapper

    return decorator
