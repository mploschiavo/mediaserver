"""Shared fixtures for guardrail tests.

Each test gets a fresh registry singleton so rule registrations from
one test don't leak into the next. The override JSON path is pinned
to a tmp directory so tests can't write into a real config root.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


_DOMAIN_MODULES = (
    "media_stack.services.guardrails.domains.storage",
    "media_stack.services.guardrails.domains.bandwidth",
    "media_stack.services.guardrails.domains.external_api",
    "media_stack.services.guardrails.domains.media_quality",
    "media_stack.services.guardrails.domains.job_health",
    "media_stack.services.guardrails.domains.auth",
    "media_stack.services.guardrails.domains.dependency",
    "media_stack.services.guardrails.domains.cost",
)


@pytest.fixture()
def fresh_registry(tmp_path, monkeypatch):
    """Reset the guardrails default registry and re-import the
    domain modules so the new singleton has every rule loaded.

    Returns the GuardrailRegistry handle so tests can poke it.
    """
    # Pin the override path BEFORE the registry is constructed.
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))

    from media_stack.services import guardrails as _guardrails_pkg
    _guardrails_pkg.reset_default()
    # Reload each domain module so its side-effect register_guardrail
    # calls fire against the fresh singleton.
    for mod_name in _DOMAIN_MODULES:
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            importlib.import_module(mod_name)
    return _guardrails_pkg.default()
