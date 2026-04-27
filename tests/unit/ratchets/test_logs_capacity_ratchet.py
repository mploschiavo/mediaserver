"""Ratchet: the Logs page must keep its "operator never has to ssh
into the container" promise.

Two invariants:

1. ``LOG_LINES_HARD_CAP`` in
   ``src/media_stack/api/services/ops.py`` stays >= 10000. The
   pre-v1.0.270 cap of 500 forced operators to ``docker logs --tail
   2000 controller`` for any non-trivial debug session — exactly the
   "fail" the user called out in the design discussion.

2. The UI exposes a 10k+ option in its limit picker so operators can
   actually USE the higher cap. Without a UI control the backend
   bump is invisible — drift between the two has happened before
   (the backend accepted up to 500 since v1.0.238 but the UI never
   sent a ``?lines=`` param at all).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OPS_PY = REPO_ROOT / "src" / "media_stack" / "api" / "services" / "ops.py"
LOGS_PAGE = REPO_ROOT / "ui" / "src" / "features" / "logs" / "LogsPage.tsx"

REQUIRED_BACKEND_CAP = 10000
REQUIRED_UI_OPTION = 10000


def test_backend_log_lines_hard_cap_meets_floor() -> None:
    text = OPS_PY.read_text(encoding="utf-8")
    m = re.search(r"LOG_LINES_HARD_CAP\s*=\s*(\d+)", text)
    assert m is not None, (
        "LOG_LINES_HARD_CAP constant missing from ops.py — the "
        "Logs handler reads it to enforce the per-request cap. "
        "Re-add the symbol; the ratchet protects its floor."
    )
    cap = int(m.group(1))
    assert cap >= REQUIRED_BACKEND_CAP, (
        f"LOG_LINES_HARD_CAP={cap} is below the {REQUIRED_BACKEND_CAP} "
        f"floor. Operators rely on a single dashboard fetch covering "
        f"the bootstrap window — dropping the cap forces them to ssh "
        f"into the controller container, which is the explicit "
        f"failure mode the v1.0.270 design fixed."
    )


def test_ui_limit_picker_offers_high_option() -> None:
    text = LOGS_PAGE.read_text(encoding="utf-8")
    m = re.search(r"LIMIT_OPTIONS\s*:.*?\[([^\]]+)\]", text, re.DOTALL)
    assert m is not None, (
        "LIMIT_OPTIONS array missing from LogsPage.tsx — the toolbar "
        "needs it to render the limit dropdown. Re-introduce."
    )
    raw = m.group(1)
    nums = [int(x) for x in re.findall(r"\d+", raw)]
    assert any(n >= REQUIRED_UI_OPTION for n in nums), (
        f"LIMIT_OPTIONS={nums} doesn't include a value at or above "
        f"{REQUIRED_UI_OPTION}. The backend cap is high enough but "
        f"operators can't request that many lines without one of the "
        f"select options matching. Add 10000 (or higher) to the array."
    )


def test_logs_page_persists_filters_to_url() -> None:
    """The URL-persisted filter state is part of the design contract:
    refresh keeps the view, share-link reproduces it. If anyone rips
    out the navigate-with-search effect, this fails."""
    text = LOGS_PAGE.read_text(encoding="utf-8")
    assert "limit:" in text and "since:" in text and "action:" in text, (
        "LogsPage must persist ``limit``, ``since``, and ``action`` "
        "filters via the URL search params. The wave-1 design called "
        "for refresh-survives-state so operators can share filtered "
        "log views with teammates. Restore the navigate(...search:...) "
        "effect that writes them through."
    )
