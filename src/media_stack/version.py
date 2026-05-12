"""Single source of truth for the media-stack package version.

The literal ``__version__`` assignment below is what hatchling reads
at build time (it scans this file for the standard Python idiom
``__version__ = "X.Y.Z"``). Hatch can't run arbitrary Python during
build, so the value MUST be a literal string here.

The repo-level ``VERSION`` file is operator-facing and unchanged —
``bin/release.sh`` bumps both atomically. The integrity check at the
bottom of this module asserts the two stay in sync at runtime; a
divergence is a release-script bug, not a deploy concern.

After Phase 12 of ADR-0001:

* Docker images set ``MEDIA_STACK_VERSION`` from this string.
* ``--version`` CLI flag reads ``media_stack.__version__``.
* Build labels (``org.opencontainers.image.version``) come from this.
* The ``VERSION`` text file remains as the operator-edited source
  but is mechanically copied into this module by ``release.sh``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO

__version__ = "1.0.359"
"""Package version. Keep in sync with ``VERSION`` at the repo root.
``bin/release.sh`` bumps both."""


class _VersionConsistencyChecker:
    """Sanity-check that ``VERSION`` on disk matches ``__version__``.

    Folded onto a class per ADR-0012 so the file has zero top-level
    function defs. Constructor-injects the on-disk path, the
    expected version literal, and the stderr stream so tests can
    swap any of them; the default construction call at module
    import wires all three to their canonical values.
    """

    def __init__(
        self,
        *,
        version_file: Path,
        expected_version: str,
        stderr: IO[str] | None = None,
    ) -> None:
        self._version_file = version_file
        self._expected_version = expected_version
        self._stderr = stderr if stderr is not None else sys.stderr

    def check(self) -> None:
        """Warn (don't crash) if VERSION on disk disagrees with the
        value baked into this module. Hit this code path only in dev:
        the wheel doesn't ship VERSION."""
        if not self._version_file.is_file():
            return  # installed wheel — no source tree
        on_disk = self._version_file.read_text(encoding="utf-8").strip()
        if on_disk and on_disk != self._expected_version:
            # Don't raise — operators reading VERSION manually will see
            # the right value, and the package's __version__ was set at
            # build time. Write to stderr so a release-tooling
            # regression surfaces instead of going silent. (Pre-existing
            # ``print()``-to-stderr path; not a new ``print`` per
            # ADR-0012 hygiene rule 8 — the original module had it.)
            print(  # noqa: T201
                f"[media_stack.version] WARNING: VERSION file ({on_disk}) "
                f"disagrees with __version__ ({self._expected_version}) — "
                f"release.sh bumped one but not the other.",
                file=self._stderr,
            )


# Module-level singleton + alias so the historical
# ``_check_version_consistency`` import surface keeps resolving for any
# external caller (tests, scripts) that imported it by name. ADR-0012
# rule 10.
_INSTANCE = _VersionConsistencyChecker(
    version_file=Path(__file__).resolve().parents[2] / "VERSION",
    expected_version=__version__,
)
_check_version_consistency = _INSTANCE.check

_INSTANCE.check()
