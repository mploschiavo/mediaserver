"""Ratchet: every profile YAML's ``auth.provider`` must reference
a service that's actually startable on the compose deploy that
profile is meant for.

The 2026-04-21 production incident: ``examples/bootstrap-profiles/
media-compose-standard.yaml`` declared ``auth.provider: authelia``
and ``auth.mode: authelia``, but the dist compose file
(``dist/docker-compose.yml``) gates the ``authelia`` service
behind ``profiles: ["auth-authelia"]``. The default
``docker compose up`` doesn't start Authelia. Envoy's ext_authz
filter then calls a non-existent upstream and fails closed →
every gateway request returns 403.

The shape of the fix: the profile must either
(a) declare an auth provider whose service is always-on (no
``profiles: [...]`` gate), or
(b) declare ``auth.provider: none`` so Envoy doesn't generate
the ext_authz filter at all.

This test fails fast if a profile claims an auth provider whose
compose service is profile-gated. To enable Authelia/Authentik in
the default install, ungate the corresponding compose service
(remove its ``profiles: [...]`` line) — that's the explicit
opt-in moment."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

import yaml  # noqa: E402

_PROFILE_DIR = ROOT / "examples" / "bootstrap-profiles"
_DIST_COMPOSE = ROOT / "dist" / "docker-compose.yml"
_DOCKER_COMPOSE = ROOT / "docker" / "docker-compose.yml"


def _gated_services_in_compose(path: Path) -> set[str]:
    """Walk a compose file and return the set of service ids that
    are gated behind one or more compose profiles. We don't care
    *which* profile — the existence of any gate means the service
    isn't started by a default ``docker compose up``."""
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    # Lightweight parse: top-level ``  <name>:`` blocks. Walk
    # each block looking for ``profiles:``.
    gated: set[str] = set()
    current: str | None = None
    indent_re = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
    profiles_re = re.compile(r"^\s+profiles:\s*\[")
    for line in text.splitlines():
        m = indent_re.match(line)
        if m:
            current = m.group(1)
            continue
        if current and profiles_re.match(line):
            gated.add(current)
    return gated


def _profile_auth_provider(profile_path: Path) -> str:
    data = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    auth = data.get("auth") or {}
    if not auth.get("enabled", False):
        return "none"
    provider = str(auth.get("provider") or auth.get("mode") or "none").strip().lower()
    return provider


# No exceptions. Every profile in examples/bootstrap-profiles/
# must declare an auth provider that's actually deployable in
# the default install path. SSO is opt-in: flip the profile +
# enable the corresponding compose profile / kustomize overlay
# at the same time.
_ALLOWED_PROFILE_MISMATCHES: dict[str, str] = {}


class ProfileAuthMatchesComposeRatchet(unittest.TestCase):

    def test_every_profile_auth_provider_is_startable_by_default(self) -> None:
        if not _DIST_COMPOSE.is_file() and not _DOCKER_COMPOSE.is_file():
            self.skipTest("no compose file found to validate against")

        # A service is "default-startable" if it appears in at
        # least one compose file without a ``profiles:`` gate.
        always_on: set[str] = set()
        for compose_path in (_DIST_COMPOSE, _DOCKER_COMPOSE):
            if not compose_path.is_file():
                continue
            text = compose_path.read_text(encoding="utf-8")
            top_level_re = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$",
                                       re.MULTILINE)
            all_services = set(top_level_re.findall(text))
            gated = _gated_services_in_compose(compose_path)
            always_on.update(all_services - gated)

        offenders: list[str] = []
        for profile_path in sorted(_PROFILE_DIR.glob("*.yaml")):
            if profile_path.name in _ALLOWED_PROFILE_MISMATCHES:
                continue
            provider = _profile_auth_provider(profile_path)
            if provider == "none":
                continue
            # ``provider: authelia`` means "the compose service
            # named authelia must be startable by default".
            if provider not in always_on:
                offenders.append(
                    f"{profile_path.name}: claims auth.provider={provider!r} "
                    "but that service is profile-gated (or absent) in "
                    "the default compose deploy. Envoy will generate "
                    "an ext_authz filter pointing at a non-existent "
                    "upstream and fail closed (403 on every request)."
                )
        self.assertFalse(
            offenders,
            "Profile/compose auth misalignment:\n  - "
            + "\n  - ".join(offenders)
            + "\n\nFix: either set auth.enabled=false + "
              "auth.provider=none in the profile (default install "
              "without SSO), or ungate the auth service in the "
              "compose file (remove its ``profiles: [...]`` line) "
              "so it starts by default.",
        )


if __name__ == "__main__":
    unittest.main()
