"""Ratchet: zero AI-tooling artefacts in commit messages.

Two patterns are banned in the message body of any commit landed
AFTER the baseline SHA in ``.ratchets/commit-message-baseline.txt``:

1. ``Co-Authored-By: Claude …`` or any other AI co-author trailer.
   The user maintains the project's authorship signals deliberately;
   bot trailers pollute contributor stats.
2. A trailing ``EOF`` line (or any ``EOF`` on its own line). This is
   the heredoc terminator from the commit-via-Bash flow leaking into
   the message body when the close marker is included by mistake.

The ratchet does NOT rewrite history. The baseline file pins the SHA
this rule starts from, so the 60+ legacy commits already on main
remain valid — only NEW commits are gated.

To advance the baseline (e.g., after a clean-up rewrite):
overwrite the SHA in ``.ratchets/commit-message-baseline.txt`` with
the new cutoff. Don't rewrite published history just to satisfy the
ratchet.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_FILE = REPO_ROOT / ".ratchets" / "commit-message-baseline.txt"

_BANNED_TRAILER_PREFIXES = (
    "Co-Authored-By:",
    "Co-authored-by:",
    "co-authored-by:",
)


def _read_baseline() -> str:
    raw = BASELINE_FILE.read_text(encoding="utf-8").strip()
    assert raw, (
        f"{BASELINE_FILE} is empty. Set it to the SHA from which "
        f"the no-trailer rule should apply (typically: the commit "
        f"that introduced this ratchet)."
    )
    return raw


def _git(*args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout


def _commit_messages_after_baseline(baseline_sha: str) -> list[tuple[str, str]]:
    """Return (sha, full_message) pairs for every commit reachable
    from HEAD that is NOT also reachable from the baseline."""
    range_spec = f"{baseline_sha}..HEAD"
    raw = _git(
        "log", range_spec, "--pretty=format:%H%x1f%B%x1e", "--no-merges",
    )
    if not raw.strip():
        return []
    out: list[tuple[str, str]] = []
    for record in raw.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        sha, _, message = record.partition("\x1f")
        out.append((sha.strip(), message.strip()))
    return out


def _looks_like_eof_marker(message: str) -> list[int]:
    """Return 1-based line numbers where a stray heredoc EOF lives.

    Plain ``EOF`` on its own line is the typical leak. We don't
    flag ``EOF`` mid-paragraph since real commit prose may
    legitimately reference 'end of file'.
    """
    bad: list[int] = []
    for idx, line in enumerate(message.splitlines(), start=1):
        if line.strip() == "EOF":
            bad.append(idx)
    return bad


def test_no_ai_coauthor_trailers_or_eof_markers_after_baseline() -> None:
    if not BASELINE_FILE.exists():
        # Without a baseline file the ratchet has no opinion about
        # any commit — that's the correct behaviour for a fresh
        # clone where the file may not yet be checked out.
        return

    baseline = _read_baseline()
    try:
        commits = _commit_messages_after_baseline(baseline)
    except subprocess.CalledProcessError as exc:
        # Shallow clones / detached states may not have the baseline
        # in their reachable history. Don't fail the build there —
        # the ratchet runs on a developer machine with full history.
        if "unknown revision" in (exc.stderr or "").lower():
            return
        raise

    failures: list[str] = []
    for sha, message in commits:
        # Only flag actual trailer LINES — i.e. lines whose stripped
        # form starts with the banned prefix. Otherwise a commit that
        # describes the rule (e.g. "rule: no Co-Authored-By: …" inside
        # a paragraph) gets caught for documenting itself.
        lines = message.splitlines()
        for line in lines:
            stripped = line.strip()
            for prefix in _BANNED_TRAILER_PREFIXES:
                if stripped.startswith(prefix):
                    failures.append(
                        f"{sha[:8]}: contains banned AI co-author trailer "
                        f"line: {stripped!r}",
                    )
                    break
            else:
                continue
            break
        eof_lines = _looks_like_eof_marker(message)
        if eof_lines:
            failures.append(
                f"{sha[:8]}: contains stray 'EOF' heredoc terminator "
                f"on line(s) {eof_lines}",
            )

    assert not failures, (
        "Commit messages must not contain AI co-author trailers or "
        "stray heredoc EOF markers. Offending commits:\n  - "
        + "\n  - ".join(failures)
        + "\n\nFix: amend the message (git commit --amend if local, or "
        "open a follow-up commit if the bad SHA is already pushed) to "
        "drop the trailer / EOF line. Do NOT advance the baseline "
        "SHA to suppress this — the rule is opt-out only via "
        "deliberate maintainer action on .ratchets/commit-message-baseline.txt."
    )
