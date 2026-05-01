"""Regression tests for ``bootstrap_config_generator``.

The generator was emitting service-id top-level keys (``sonarr``,
``radarr``, etc.) that the validator rejects, because its schema
lookup hit a dev-relative path (``contracts/../src/...``) that
doesn't exist inside the runtime container. With ``allowed_keys``
empty, the per-service guard fell through and every contract's
defaults block was emitted as a top-level key.

This module pins two invariants:

1. The generator's view of allowed keys equals the validator's.
   No more "the validator says X is allowed but the generator
   thinks the schema is empty" drift.
2. Every top-level key the generator produces passes
   ``TopLevelBootstrapConfig.from_dict``. This is the bug the
   user actually hit on first deploy of v1.0.287 — the bootstrap
   ran 71s, errored on the legacy pipeline, and left the *arr
   stack with no API keys distributed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_stack.infrastructure.jobs.bootstrap_config_generator import (
    GenerateBootstrapConfigCommand,
)
from media_stack.services.top_level_config_model import (
    TopLevelBootstrapConfig,
    _load_top_level_schema,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = REPO_ROOT / "contracts"


@pytest.fixture
def generated_config(tmp_path: Path) -> dict:
    """Run the generator against the real contracts dir + profile."""
    profile_path = CONTRACTS_DIR / "media-stack.profile.yaml"
    output_path = tmp_path / "media-stack.config.json"
    cmd = GenerateBootstrapConfigCommand()
    return cmd.generate(
        contracts_dir=CONTRACTS_DIR,
        profile_path=profile_path if profile_path.is_file() else None,
        output_path=output_path,
    )


def test_generator_emits_only_keys_the_validator_accepts(
    generated_config: dict,
) -> None:
    """Every top-level key emitted MUST be in the validator's
    allowed_keys list — otherwise the legacy pipeline rejects the
    config and bootstrap fails partway through."""
    allowed, _required = _load_top_level_schema()
    emitted = set(generated_config.keys())
    forbidden = sorted(emitted - set(allowed.keys()))
    assert not forbidden, (
        f"bootstrap_config_generator emitted top-level keys not in the "
        f"validator's allowed_keys: {forbidden}. Either add them to "
        f"src/media_stack/contracts/top_level_config_schema.json, or "
        f"stop emitting them in the generator."
    )


def test_generated_config_passes_validator(generated_config: dict) -> None:
    """End-to-end: feed the generator output through the validator
    that ``run-legacy-pipeline`` uses. Catches the original bug
    the moment it would re-occur."""
    # ``TopLevelBootstrapConfig.from_dict`` raises on any drift.
    TopLevelBootstrapConfig.from_dict(generated_config)


def test_generator_loads_same_schema_as_validator(tmp_path: Path) -> None:
    """The generator should pick up the validator's schema regardless
    of contracts-dir layout. Pointing at a fresh empty contracts dir
    (no ``services/`` yamls, no dev-relative ``src/...`` ancestor)
    must NOT silently leave allowed_keys empty — that's the failure
    mode the original bug shipped on."""
    fake_contracts = tmp_path / "fake-contracts"
    fake_contracts.mkdir()
    (fake_contracts / "services").mkdir()
    cmd = GenerateBootstrapConfigCommand()
    out_path = tmp_path / "out.json"
    config = cmd.generate(
        contracts_dir=fake_contracts,
        profile_path=None,
        output_path=out_path,
    )
    # Generator should still emit a valid baseline.
    assert config.get("config_version") == 2
    # And every emitted key should be in the validator's allowed list,
    # regardless of layout.
    allowed, _required = _load_top_level_schema()
    forbidden = sorted(set(config.keys()) - set(allowed.keys()))
    assert not forbidden, (
        f"empty-contracts-dir layout emitted forbidden keys: {forbidden}"
    )
