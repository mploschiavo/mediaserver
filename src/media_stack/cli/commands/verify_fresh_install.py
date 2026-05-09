"""Promise-driven fresh-install verifier CLI (ADR-0004 Phase 6.3).

Console-script: ``media-stack-verify`` (after ``pip install``).
Module path:    ``python -m media_stack.cli.commands.verify_fresh_install``.

Wraps ``FreshInstallVerifier`` from
``media_stack.application.verifier.fresh_install``. Replaces
``media-stack-probe-promises`` as the engine behind
``bin/test/verify-fresh-install.sh``: that script handles the
optional wipe + bring-up, then exec's this CLI for the actual
probe report.

Why a new CLI instead of adding flags to the legacy one: the legacy
CLI re-implements the probe loop from outside the controller (every
URL goes through host:port mappings, every assertion runs in the
operator's Python). This CLI is a thin client of the controller's
``/api/orchestrator/promises/state`` endpoint -- operator and
orchestrator agree by construction.

Exit codes (matching the legacy CLI's contract):
    0 -- every applicable promise probed ok
    1 -- at least one promise failed / unknown
    2 -- controller unreachable, state not yet persisted, or stale

Output:
    default (text)  -- one summary line + one line per non-ok promise
    --json          -- machine-readable VerificationResult dump

Flag shape matches what ``verify-fresh-install.sh`` already passes
to the legacy CLI so the Phase 6.4 swap is one line of shell.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any

from media_stack.application.verifier.fresh_install import (
    FreshInstallVerifier,
    VerificationResult,
)


_DEFAULT_CONTROLLER_URL = "http://localhost:9100"


class VerifyFreshInstallCommand:
    """ADR-0012 class-shaped CLI entrypoint for ``media-stack-verify``.

    All helpers are plain instance methods. ``main`` dispatches the
    ``FreshInstallVerifier`` constructor through ``sys.modules[__name__]``
    so existing tests that ``patch.object(cli, "FreshInstallVerifier", ...)``
    still intercept the call.
    """

    def result_to_jsonable(
        self, result: VerificationResult,
    ) -> dict[str, Any]:
        """Convert dataclass result + nested attempt tuples to JSON-safe dict."""
        payload = dataclasses.asdict(result)
        # asdict turns tuples into lists, which is what we want for JSON.
        return payload

    def filter_attempts(
        self, result: VerificationResult, substr: str,
    ) -> VerificationResult:
        """Narrow the result to attempts whose promise_id contains ``substr``.

        Acceptance is recomputed against the filtered set so the CLI can
        answer narrow questions ("is bazarr healthy right now?") without
        rebuilding the report by hand.
        """
        if not substr:
            return result

        filtered_passed = tuple(
            a for a in result.passed_attempts if substr in a.promise_id
        )
        failed = tuple(a for a in result.failed if substr in a.promise_id)
        skipped = tuple(a for a in result.skipped if substr in a.promise_id)
        unknown = tuple(a for a in result.unknown if substr in a.promise_id)
        visible_total = (
            len(filtered_passed) + len(failed) + len(skipped) + len(unknown)
        )
        is_pass = (
            len(failed) == 0 and len(unknown) == 0 and visible_total > 0
        )

        lines = [
            f"orchestrator (filter='{substr}'): {len(filtered_passed)}"
            f"/{visible_total} promises ok"
            f" (platform={result.platform or 'unknown'})",
        ]
        for a in failed:
            lines.append(f"  FAIL  {a.promise_id}: {a.detail or a.status}")
        for a in unknown:
            lines.append(f"  UNK   {a.promise_id}: {a.detail or 'unknown'}")
        for a in skipped:
            lines.append(f"  SKIP  {a.promise_id}: {a.detail or a.status}")

        return VerificationResult(
            started_at=result.started_at,
            elapsed_seconds=result.elapsed_seconds,
            total=visible_total,
            passed=len(filtered_passed),
            failed=failed,
            skipped=skipped,
            unknown=unknown,
            passed_attempts=filtered_passed,
            is_acceptance_pass=is_pass,
            saved_at=result.saved_at,
            last_tick_age_seconds=result.last_tick_age_seconds,
            platform=result.platform,
            controller_reachable=result.controller_reachable,
            error=result.error,
            detail_lines=tuple(lines),
        )

    def exit_code(self, result: VerificationResult) -> int:
        if not result.controller_reachable:
            return 2
        if result.error and result.total == 0:
            # 503 / state-not-yet path -- distinguishable from "1 promise
            # failed" so CI can distinguish "stack still booting" from
            # "stack came up but is broken".
            return 2
        if result.is_acceptance_pass:
            return 0
        return 1

    def print_text(self, result: VerificationResult) -> None:
        for line in result.detail_lines:
            print(line)
        if result.is_acceptance_pass:
            print(f"\n{result.passed}/{result.total} promises pass")
        elif result.total == 0:
            # Unreachable / 503 / stale -- error already in detail_lines.
            pass
        else:
            print(
                f"\n{result.passed}/{result.total} promises pass "
                f"({len(result.failed)} failed, {len(result.unknown)} unknown, "
                f"{len(result.skipped)} skipped)",
            )

    def main(self, argv: list[str] | None = None) -> int:
        p = argparse.ArgumentParser(
            prog="media-stack-verify",
            description=(
                "Verify a media-stack deploy by reading the orchestrator's "
                "persisted promise state. Exit 0 if every applicable "
                "promise is ok, 1 if any failed, 2 if the controller "
                "isn't reachable or its state isn't yet persisted."
            ),
        )
        p.add_argument(
            "--controller-url", default=_DEFAULT_CONTROLLER_URL,
            help=(
                f"Controller API base URL (default: {_DEFAULT_CONTROLLER_URL}). "
                "Honors $CONTROLLER_URL when --controller-url is omitted."
            ),
        )
        p.add_argument("--admin-user", default="",
                       help="Basic auth user. Honors $ADMIN_USER.")
        p.add_argument("--admin-pass", default="",
                       help="Basic auth password. Honors $ADMIN_PASS.")
        p.add_argument(
            "--filter", default="",
            help="Substring match on promise id; restricts the report to "
                 "matching promises. Acceptance recomputes against the "
                 "filtered set.",
        )
        p.add_argument(
            "--wait", type=float, default=0.0,
            help="Poll up to N seconds for the orchestrator to converge. "
                 "0 (default) = single-shot. Fail-fast on any "
                 "failed_permanent.",
        )
        p.add_argument(
            "--poll-interval", type=float, default=5.0,
            help="Seconds between polls when --wait is set.",
        )
        p.add_argument(
            "--timeout", type=float, default=30.0,
            help="HTTP timeout per request (seconds).",
        )
        p.add_argument(
            "--require-fresh-within", type=float, default=90.0,
            help="Treat orchestrator state older than this as not-yet-fresh "
                 "(default: 90s). The endpoint applies a separate 120s "
                 "threshold; this is the verifier's tighter gate.",
        )
        p.add_argument("--json", action="store_true",
                       help="machine-readable VerificationResult JSON output")

        # Compatibility shims -- the legacy CLI accepted these. Kept so
        # the wrapper script's flag list works unchanged across the
        # switchover.
        p.add_argument("--compose-file", default="",
                       help=argparse.SUPPRESS)  # accepted, unused
        p.add_argument("--k8s", action="store_true", help=argparse.SUPPRESS)
        p.add_argument("--unified", action="store_true",
                       help=argparse.SUPPRESS)

        args = p.parse_args(argv)

        controller_url = (
            args.controller_url
            or os.environ.get("CONTROLLER_URL")
            or _DEFAULT_CONTROLLER_URL
        )
        admin_user = args.admin_user or os.environ.get("ADMIN_USER") or ""
        admin_pass = args.admin_pass or os.environ.get("ADMIN_PASS") or ""

        # Dispatch through sys.modules so tests that
        # ``patch.object(cli, "FreshInstallVerifier", ...)`` still
        # intercept the constructor.
        module = sys.modules[__name__]
        verifier = module.FreshInstallVerifier(
            controller_url=controller_url,
            admin_user=admin_user,
            admin_pass=admin_pass,
            require_fresh_tick_within_seconds=args.require_fresh_within,
            timeout_seconds=args.timeout,
        )

        if args.wait > 0.0:
            result = verifier.wait_for_steady_state(
                max_wait_seconds=args.wait,
                poll_interval_seconds=args.poll_interval,
            )
        else:
            result = verifier.verify()

        if args.filter:
            result = self.filter_attempts(result, args.filter)

        if args.json:
            print(json.dumps(
                self.result_to_jsonable(result), indent=2, default=str,
            ))
        else:
            self.print_text(result)

        return self.exit_code(result)


_INSTANCE = VerifyFreshInstallCommand()
main = _INSTANCE.main
result_to_jsonable = _INSTANCE.result_to_jsonable
filter_attempts = _INSTANCE.filter_attempts
exit_code = _INSTANCE.exit_code
print_text = _INSTANCE.print_text


if __name__ == "__main__":
    sys.exit(main())
