"""Promise-driven fresh-install verifier (see ADR-0004).

External-client mode: HTTP to the controller's
``/api/orchestrator/promises/state`` endpoint, which serves the
orchestrator's most recent persisted tick. The verifier summarizes
that snapshot into a ``VerificationResult`` the operator (or CI)
can act on.

Why an API client and not a parallel probe loop:

  * The orchestrator is the live source of truth. Anything that
    re-probes from outside the controller has different DNS, TLS
    posture, and timing — it can pass while the live pipeline is
    failing (or vice versa). The verifier's whole point is to give
    operators the same answer the auto-heal cycle is using.
  * One probe-dispatch table, not two. Whatever bugs the
    orchestrator has, the verifier sees the same — by design.

``wait_for_steady_state`` polls until the orchestrator converges,
times out, or any promise turns ``failed_permanent`` (fail-fast on
operator-required state — pounding on a misconfigured stack for
five minutes won't fix it).
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional


logger = logging.getLogger(__name__)


_DEFAULT_REQUIRE_FRESH_WITHIN_SECONDS = 90.0
_DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0
_DEFAULT_WAIT_MAX_SECONDS = 300.0
_DEFAULT_WAIT_POLL_INTERVAL_SECONDS = 5.0


# Mirrors the persisted ``PromiseAttempt`` shape but lives in the
# verifier layer so external CI consumers don't have to import the
# domain package.
@dataclass(frozen=True)
class VerifierAttempt:
    promise_id: str
    status: str
    started_at: float
    elapsed_seconds: float
    detail: str
    probe_evidence: dict[str, Any]
    ensurer_fired: bool
    ensurer_attempts: int
    consecutive_failures: int

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> "VerifierAttempt":
        return cls(
            promise_id=str(raw.get("promise_id") or ""),
            status=str(raw.get("status") or "unknown"),
            started_at=float(raw.get("started_at") or 0.0),
            elapsed_seconds=float(raw.get("elapsed_seconds") or 0.0),
            detail=str(raw.get("detail") or ""),
            probe_evidence=dict(raw.get("probe_evidence") or {}),
            ensurer_fired=bool(raw.get("ensurer_fired") or False),
            ensurer_attempts=int(raw.get("ensurer_attempts") or 0),
            consecutive_failures=int(raw.get("consecutive_failures") or 0),
        )


@dataclass(frozen=True)
class VerificationResult:
    """One verification cycle's full report.

    ``is_acceptance_pass`` is the single bool the shell script /
    CI cares about — true iff every applicable promise probed ``ok``.
    ``detail_lines`` is a human-readable summary the CLI prints
    above the exit code.
    """

    started_at: float
    elapsed_seconds: float
    total: int
    passed: int
    failed: tuple[VerifierAttempt, ...] = field(default_factory=tuple)
    skipped: tuple[VerifierAttempt, ...] = field(default_factory=tuple)
    unknown: tuple[VerifierAttempt, ...] = field(default_factory=tuple)
    passed_attempts: tuple[VerifierAttempt, ...] = field(default_factory=tuple)
    is_acceptance_pass: bool = False
    detail_lines: tuple[str, ...] = field(default_factory=tuple)
    saved_at: Optional[float] = None
    last_tick_age_seconds: Optional[float] = None
    platform: str = ""
    controller_reachable: bool = True
    error: Optional[str] = None


class FreshInstallVerifier:
    """Promise-driven verifier for fresh-install acceptance.

    External-client mode — connects to the controller's API and
    reads the orchestrator's latest tick. Used by
    ``verify-fresh-install.sh`` from the operator's host shell.
    """

    def __init__(
        self,
        *,
        controller_url: str,
        admin_user: str = "",
        admin_pass: str = "",
        require_fresh_tick_within_seconds: float = _DEFAULT_REQUIRE_FRESH_WITHIN_SECONDS,
        timeout_seconds: float = _DEFAULT_HTTP_TIMEOUT_SECONDS,
        url_opener: Optional[Any] = None,
    ) -> None:
        if not controller_url:
            raise ValueError("controller_url is required")
        self._controller_url = controller_url.rstrip("/")
        self._admin_user = admin_user
        self._admin_pass = admin_pass
        self._require_fresh_within = float(require_fresh_tick_within_seconds)
        self._timeout = float(timeout_seconds)
        # Test injection point. Defaults to urllib.request's module-
        # level urlopen; tests pass a callable returning a fake
        # response object with read() + getcode() + headers.
        self._url_opener = url_opener or urllib.request.urlopen

    # --- public surface -----------------------------------------------------

    def verify(self) -> VerificationResult:
        """One verification cycle.

        Always returns a result; connection / auth errors land on the
        result as ``controller_reachable=False`` + ``error=...``
        rather than raising — CI and the shell script can branch on
        the exit code without exception handling.
        """
        started_at = time.time()
        status_code, body = self._fetch_state()
        elapsed = time.time() - started_at

        if status_code is None:
            return VerificationResult(
                started_at=started_at,
                elapsed_seconds=elapsed,
                total=0,
                passed=0,
                is_acceptance_pass=False,
                controller_reachable=False,
                error=str(body) if body else "controller unreachable",
                detail_lines=(
                    f"controller unreachable at {self._controller_url}: "
                    f"{body or 'unknown error'}",
                ),
            )

        saved_at = body.get("saved_at") if isinstance(body, dict) else None
        age = body.get("last_tick_age_seconds") if isinstance(body, dict) else None
        platform = (body.get("platform") if isinstance(body, dict) else "") or ""

        if status_code == 503:
            err = (body or {}).get("error") if isinstance(body, dict) else "stale"
            return VerificationResult(
                started_at=started_at,
                elapsed_seconds=elapsed,
                total=0,
                passed=0,
                is_acceptance_pass=False,
                saved_at=saved_at if isinstance(saved_at, (int, float)) else None,
                last_tick_age_seconds=age if isinstance(age, (int, float)) else None,
                platform=platform,
                controller_reachable=True,
                error=str(err or "orchestrator state unavailable"),
                detail_lines=(
                    f"orchestrator state unavailable: {err}",
                ),
            )

        if status_code != 200 or not isinstance(body, dict):
            return VerificationResult(
                started_at=started_at,
                elapsed_seconds=elapsed,
                total=0,
                passed=0,
                is_acceptance_pass=False,
                controller_reachable=True,
                error=f"unexpected status {status_code}",
                detail_lines=(
                    f"unexpected response status {status_code}",
                ),
            )

        # 200 path — staleness re-check against the verifier's own
        # threshold (the endpoint also returns 503 above the endpoint's
        # threshold; the verifier may want a tighter one).
        if (isinstance(age, (int, float))
                and float(age) > self._require_fresh_within):
            return VerificationResult(
                started_at=started_at,
                elapsed_seconds=elapsed,
                total=0,
                passed=0,
                is_acceptance_pass=False,
                saved_at=float(saved_at) if isinstance(saved_at, (int, float)) else None,
                last_tick_age_seconds=float(age),
                platform=platform,
                controller_reachable=True,
                error=(
                    f"orchestrator tick is {age:.0f}s old; "
                    f"require fresh within {self._require_fresh_within:.0f}s"
                ),
                detail_lines=(
                    f"orchestrator tick is {age:.0f}s old; verifier "
                    f"requires fresh within {self._require_fresh_within:.0f}s",
                ),
            )

        return self._summarize(
            body, started_at=started_at, elapsed=elapsed,
            saved_at=saved_at, age=age, platform=platform,
        )

    def wait_for_steady_state(
        self,
        *,
        max_wait_seconds: float = _DEFAULT_WAIT_MAX_SECONDS,
        poll_interval_seconds: float = _DEFAULT_WAIT_POLL_INTERVAL_SECONDS,
        sleep: Optional[Any] = None,
    ) -> VerificationResult:
        """Poll until acceptance passes, timeout, or fail-fast.

        Fail-fast triggers the moment any promise reaches
        ``failed_permanent``. That's an operator-config bug (e.g.
        missing ``QBITTORRENT_PASSWORD``) — pounding on it for the
        full timeout doesn't help and just delays the error.

        ``sleep`` is injectable so tests don't actually sleep.
        """
        sleep_fn = sleep or time.sleep
        deadline = time.time() + float(max_wait_seconds)
        last: VerificationResult = VerificationResult(
            started_at=time.time(),
            elapsed_seconds=0.0,
            total=0,
            passed=0,
            is_acceptance_pass=False,
            error="never polled",
            detail_lines=("wait_for_steady_state: no poll attempted",),
        )

        while True:
            last = self.verify()
            if last.is_acceptance_pass:
                return last
            if any(a.status == "failed_permanent" for a in last.failed):
                return last
            if time.time() >= deadline:
                return last
            sleep_fn(float(poll_interval_seconds))

    # --- internals ----------------------------------------------------------

    def _fetch_state(self) -> tuple[Optional[int], Any]:
        """Return ``(status_code, body)`` or ``(None, error_message)``
        when the controller is unreachable. Body is the parsed JSON
        when ``Content-Type: application/json``, else raw text."""
        url = f"{self._controller_url}/api/orchestrator/promises/state"
        req = urllib.request.Request(url, method="GET")
        if self._admin_user or self._admin_pass:
            token = base64.b64encode(
                f"{self._admin_user}:{self._admin_pass}".encode("utf-8"),
            ).decode("ascii")
            req.add_header("Authorization", f"Basic {token}")
        req.add_header("Accept", "application/json")

        try:
            with self._url_opener(req, timeout=self._timeout) as resp:
                code = int(resp.getcode())
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            # Non-200 path — body still useful (503 carries the staleness fields).
            try:
                body_bytes = exc.read()
            except Exception:  # noqa: BLE001
                body_bytes = b""
            try:
                return int(exc.code), json.loads(body_bytes or b"{}")
            except json.JSONDecodeError:
                return int(exc.code), body_bytes.decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.debug("verifier: controller unreachable: %s", exc)
            return None, f"{exc.__class__.__name__}: {exc}"

        try:
            return code, json.loads(raw)
        except json.JSONDecodeError:
            return code, raw.decode("utf-8", "replace")

    def _summarize(
        self,
        body: dict[str, Any],
        *,
        started_at: float,
        elapsed: float,
        saved_at: Any,
        age: Any,
        platform: str,
    ) -> VerificationResult:
        attempts_raw = body.get("attempts") or []
        attempts = tuple(
            VerifierAttempt.from_payload(a)
            for a in attempts_raw
            if isinstance(a, dict)
        )
        passed = tuple(a for a in attempts if a.status == "ok")
        failed = tuple(
            a for a in attempts
            if a.status in ("failed_transient", "failed_permanent", "dep_failed")
        )
        skipped = tuple(
            a for a in attempts
            if a.status in ("skipped_cooldown", "skipped_platform")
        )
        unknown = tuple(a for a in attempts if a.status == "unknown")

        is_pass = len(failed) == 0 and len(unknown) == 0 and len(attempts) > 0

        lines = [
            f"orchestrator: {len(passed)}/{len(attempts)} promises ok"
            f" (platform={platform or 'unknown'})",
        ]
        for a in failed:
            lines.append(f"  FAIL  {a.promise_id}: {a.detail or a.status}")
        for a in unknown:
            lines.append(f"  UNK   {a.promise_id}: {a.detail or 'unknown'}")
        for a in skipped:
            lines.append(f"  SKIP  {a.promise_id}: {a.detail or a.status}")

        return VerificationResult(
            started_at=started_at,
            elapsed_seconds=elapsed,
            total=len(attempts),
            passed=len(passed),
            failed=failed,
            skipped=skipped,
            unknown=unknown,
            passed_attempts=passed,
            is_acceptance_pass=is_pass,
            saved_at=float(saved_at) if isinstance(saved_at, (int, float)) else None,
            last_tick_age_seconds=float(age) if isinstance(age, (int, float)) else None,
            platform=platform,
            controller_reachable=True,
            detail_lines=tuple(lines),
        )


__all__ = ["FreshInstallVerifier", "VerificationResult", "VerifierAttempt"]
