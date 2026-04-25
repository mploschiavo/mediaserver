"""Generate a per-job test skeleton.

Console-script: ``media-stack-scaffold-job-test`` (after
``pip install``). Module path:
``python -m media_stack.cli.commands.scaffold_job_test``.

Usage::

    media-stack-scaffold-job-test <job-name>

Emits ``tests/jobs/test_<job_name>.py`` with the four scenarios
every job is expected to cover (success, service-unreachable,
partial-skip, idempotency). Reviewers can point new contributors
at the generated file as the canonical pattern; the generator
itself is the executable form of ``tests/jobs/README.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


_TEMPLATE = '''"""Tests for the ``{job_name}`` job.

Generated from ``media-stack-scaffold-job-test`` — fill in the four
scenarios below. The pattern is documented in
``tests/jobs/README.md``.

Cover:

1. Success path — happy run, ``status: ok``, expected side
   effects observed.
2. Service-unreachable path — upstream is down. The job must
   *not* raise; it should return ``status: error`` with a clear
   error string (or ``status: skipped`` if the contract allows).
3. Partial-skip path — some sub-tasks succeed, some are skipped
   (e.g., 3 of 5 *arrs configured because the others have no
   key yet). The job must not blow up.
4. Idempotency — running the job twice has the same effect as
   running it once. Re-running should be cheap and safe.
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock as _mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


JOB_NAME = "{job_name}"


class _TodoMixin:
    def _todo(self, scenario: str) -> None:
        # Replace this with a real assertion once the fixture
        # set has been built. Keeping the placeholder is a soft
        # ratchet — the bare scaffold surfaces in CI as a skip
        # for every untouched scenario.
        self.skipTest(f"unimplemented: {{scenario}} for {{JOB_NAME}}")


class {class_name}SuccessTests(unittest.TestCase, _TodoMixin):

    def test_happy_path(self) -> None:
        """Happy run: every prerequisite present, side effects
        observed (e.g. config files updated, env populated)."""
        self._todo("success")


class {class_name}UnreachableTests(unittest.TestCase, _TodoMixin):

    def test_service_unreachable(self) -> None:
        """Upstream service is down — job must not raise; it
        should report the error in its result dict."""
        self._todo("service-unreachable")


class {class_name}PartialSkipTests(unittest.TestCase, _TodoMixin):

    def test_partial_skip(self) -> None:
        """Some sub-tasks succeed, some are skipped because their
        prerequisite isn't ready yet. Job must remain stable."""
        self._todo("partial-skip")


class {class_name}IdempotencyTests(unittest.TestCase, _TodoMixin):

    def test_running_twice_is_a_noop(self) -> None:
        """Run the job twice; the second run must produce the
        same observable state as the first."""
        self._todo("idempotency")


if __name__ == "__main__":
    unittest.main()
'''


def _camel(name: str) -> str:
    return "".join(p.capitalize() for p in name.replace("-", "_").split("_"))


def _safe(name: str) -> str:
    return name.replace("-", "_")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_name", help="job name (e.g. discover-api-keys)")
    parser.add_argument(
        "--out-dir",
        # File now lives at src/media_stack/cli/commands/<this>.py —
        # parents[4] lands at the repo root. Was parents[1] when the
        # script lived at bin/<this>.py.
        default=str(Path(__file__).resolve().parents[4] / "tests" / "jobs"),
        help="destination directory (defaults to tests/jobs)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite an existing file at the destination",
    )
    args = parser.parse_args(argv)

    target = Path(args.out_dir) / f"test_{_safe(args.job_name)}.py"
    if target.exists() and not args.force:
        print(f"refusing to overwrite {target} (use --force)",
              file=sys.stderr)
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    body = _TEMPLATE.format(
        job_name=args.job_name,
        class_name=_camel(args.job_name),
    )
    target.write_text(body, encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
