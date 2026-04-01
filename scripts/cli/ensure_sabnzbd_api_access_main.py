"""Compatibility wrapper for moved app-scoped CLI implementation."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType


def _load_impl() -> ModuleType:
    scripts_dir = Path(__file__).resolve().parents[1]
    scripts_dir_text = str(scripts_dir)
    if scripts_dir_text not in sys.path:
        sys.path.insert(0, scripts_dir_text)
    return importlib.import_module(
        "bootstrap_services.apps.sabnzbd.cli.ensure_sabnzbd_api_access_main"
    )


_IMPL = _load_impl()


def __getattr__(name: str):
    return getattr(_IMPL, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_IMPL)))


def main() -> int:
    main_fn = getattr(_IMPL, "main", None)
    if main_fn is None:
        raise AttributeError("Target module does not define main()")
    return int(main_fn())


if __name__ == "__main__":
    raise SystemExit(main())
