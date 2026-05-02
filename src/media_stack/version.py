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

from pathlib import Path

__version__ = "1.0.314"
"""Package version. Keep in sync with ``VERSION`` at the repo root.
``bin/release.sh`` bumps both."""


def _check_version_consistency() -> None:
    """Sanity check: warn (don't crash) if VERSION on disk disagrees
    with the value baked into this module. Hit this code path only
    in dev: the wheel doesn't ship VERSION."""
    repo_version = Path(__file__).resolve().parents[2] / "VERSION"
    if not repo_version.is_file():
        return  # installed wheel — no source tree
    on_disk = repo_version.read_text(encoding="utf-8").strip()
    if on_disk and on_disk != __version__:
        # Don't raise — operators reading VERSION manually will see
        # the right value, and the package's __version__ was set at
        # build time. Log to stderr so a release-tooling regression
        # surfaces instead of going silent.
        import sys
        print(
            f"[media_stack.version] WARNING: VERSION file ({on_disk}) "
            f"disagrees with __version__ ({__version__}) — "
            f"release.sh bumped one but not the other.",
            file=sys.stderr,
        )


_check_version_consistency()
