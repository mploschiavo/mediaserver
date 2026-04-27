#!/usr/bin/env python3
"""Diagnose a broken Jellyseerr-Authelia (or other OIDC client) login.

The most common failure mode reported as "An error occurred while
logging in with Authelia" is one of:

  1. The generated pbkdf2 client_secret hash doesn't actually
     verify the plaintext shipped to the downstream app — a
     regression in the encoder that unit tests pass but the live
     Authelia binary rejects at startup.
  2. The downstream app's discovery URL (the issuerUrl) doesn't
     resolve from inside its container (DNS or extra_hosts gap).
  3. The redirect_uri the downstream app sends doesn't EXACTLY
     match any URI registered in the Authelia client config
     (trailing slash, host case, port).
  4. The token-endpoint auth method on either side is mismatched
     (Authelia: ``client_secret_post`` vs Jellyseerr: ``basic``).

This CLI walks each check in turn and prints a clear pass/fail.
Read-only — no config writes, no probes that mutate.

Usage:

  bin/ops/verify_authelia_oidc.py
  bin/ops/verify_authelia_oidc.py --client jellyseerr
  bin/ops/verify_authelia_oidc.py --authelia-config /etc/authelia/conf.yml
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

DEFAULT_AUTHELIA_CONFIG = (
    REPO_ROOT / "config" / "authelia" / "configuration.yml"
)
DEFAULT_OIDC_CONTRACT = (
    REPO_ROOT / "contracts" / "auth" / "oidc_clients.yaml"
)
DEFAULT_JELLYSEERR_SETTINGS = (
    REPO_ROOT / "config" / "jellyseerr" / "settings.json"
)


def _ok(msg: str) -> None:
    print(f"  [OK ] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _info(msg: str) -> None:
    print(f"  [INFO] {msg}")


def _read_yaml(path: Path) -> dict | None:
    try:
        import yaml
    except ImportError:
        print(f"[ERR] PyYAML required for parsing {path}", file=sys.stderr)
        return None
    if not path.is_file():
        print(f"[ERR] {path} not found", file=sys.stderr)
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def check_pbkdf2_roundtrip(
    contract_clients: list[dict],
    authelia_clients: list[dict],
    target_client_id: str,
) -> bool:
    """Step 1 — pull the plaintext from the contract YAML, find
    the hashed entry in the live Authelia config, and verify
    they match. The plaintext is what the downstream app sends in
    the token-exchange POST; the hash is what Authelia compares
    against."""
    print(f"\n[1/4] Verifying pbkdf2 hash for client '{target_client_id}'…")
    plaintext = None
    for c in contract_clients:
        if c.get("client_id") == target_client_id:
            plaintext = c.get("client_secret")
            break
    if plaintext is None:
        _fail(f"client_id '{target_client_id}' missing from contract YAML")
        return False

    hashed = None
    for c in authelia_clients:
        if c.get("client_id") == target_client_id:
            hashed = c.get("client_secret")
            break
    if hashed is None:
        _fail(
            f"client_id '{target_client_id}' missing from live "
            "Authelia config",
        )
        return False
    if not hashed.startswith("$pbkdf2-sha512$"):
        _fail(
            f"client_secret in live config doesn't look like a "
            f"pbkdf2 hash: {hashed[:20]}…",
        )
        return False

    from media_stack.infrastructure.auth.authelia_oidc_crypto import (
        OidcCrypto,
    )
    if OidcCrypto.verify_pbkdf2(plaintext, hashed):
        _ok(
            "pbkdf2 hash in live config verifies the plaintext "
            "from the contract YAML",
        )
        return True
    _fail(
        "pbkdf2 hash MISMATCH — the live Authelia config was "
        "generated against a different plaintext than what the "
        "downstream app currently uses. Re-run the configure-auth "
        "job to regenerate, then restart Authelia.",
    )
    return False


def check_token_endpoint_auth_method(
    authelia_clients: list[dict], target_client_id: str,
) -> bool:
    print("\n[2/4] Checking token_endpoint_auth_method…")
    entry = next(
        (c for c in authelia_clients if c.get("client_id") == target_client_id),
        None,
    )
    if entry is None:
        _fail("client missing from live Authelia config")
        return False
    method = entry.get("token_endpoint_auth_method", "client_secret_basic")
    if method == "client_secret_post":
        _ok(f"token_endpoint_auth_method = {method!r}")
        return True
    _warn(
        f"token_endpoint_auth_method = {method!r} — Jellyseerr's "
        "preview-OIDC build sends the secret via POST body. If the "
        "two sides disagree, the token exchange returns "
        "401 invalid_client.",
    )
    return False


def check_redirect_uris_have_no_typos(
    authelia_clients: list[dict], target_client_id: str,
) -> bool:
    print("\n[3/4] Checking redirect_uris…")
    entry = next(
        (c for c in authelia_clients if c.get("client_id") == target_client_id),
        None,
    )
    if entry is None:
        _fail("client missing from live Authelia config")
        return False
    uris = entry.get("redirect_uris", [])
    if not uris:
        _fail("redirect_uris is empty — Authelia rejects the request")
        return False
    bad = []
    for u in uris:
        if not isinstance(u, str):
            bad.append(("non-string entry", u))
            continue
        if not (u.startswith("http://") or u.startswith("https://")):
            bad.append(("missing scheme", u))
        if " " in u:
            bad.append(("whitespace", u))
        # Trailing slashes are case-sensitive matches against the
        # downstream app's redirect_uri parameter. Both sides must
        # agree byte-for-byte; flag entries that have or lack one.
    if bad:
        for reason, u in bad:
            _fail(f"{reason}: {u}")
        return False
    _ok(f"{len(uris)} redirect_uri(s) all parse cleanly")
    for u in uris:
        _info(u)
    return True


def check_jellyseerr_plaintext_matches_contract(
    contract_clients: list[dict],
    settings_path: Path,
) -> bool:
    """Step 4 — Jellyseerr-specific: confirm the plaintext in
    settings.json matches the contract's plaintext. If they
    drift, the token exchange will fail no matter how perfect the
    Authelia config is."""
    print(
        f"\n[4/4] Cross-checking Jellyseerr settings.json against the "
        f"contract…",
    )
    if not settings_path.is_file():
        _warn(
            f"{settings_path} not found — Jellyseerr may not be "
            f"configured yet",
        )
        return False
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"unable to read {settings_path}: {exc}")
        return False
    providers = (settings.get("oidc") or {}).get("providers", []) or []
    authelia = next(
        (p for p in providers if (p or {}).get("slug") == "authelia"),
        None,
    )
    if authelia is None:
        _warn("no Authelia provider configured in Jellyseerr settings")
        return False
    sent_plaintext = authelia.get("clientSecret")
    contract = next(
        (c for c in contract_clients if c.get("client_id") == "jellyseerr"),
        None,
    )
    if contract is None:
        _fail("contract YAML has no jellyseerr entry")
        return False
    if sent_plaintext == contract.get("client_secret"):
        _ok("Jellyseerr settings.json plaintext matches the contract")
        return True
    _fail(
        "Jellyseerr settings.json sends a DIFFERENT plaintext than "
        "the contract. Sync them or the token exchange always 401s.",
    )
    return False


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Diagnose Authelia-OIDC integration drift for a "
            "downstream app (Jellyseerr by default)."
        ),
    )
    p.add_argument("--client", default="jellyseerr")
    p.add_argument(
        "--authelia-config", type=Path,
        default=DEFAULT_AUTHELIA_CONFIG,
    )
    p.add_argument(
        "--oidc-contract", type=Path,
        default=DEFAULT_OIDC_CONTRACT,
    )
    p.add_argument(
        "--jellyseerr-settings", type=Path,
        default=DEFAULT_JELLYSEERR_SETTINGS,
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    print(f"==> Verifying OIDC integration for client '{args.client}'")
    print(f"    Authelia config: {args.authelia_config}")
    print(f"    OIDC contract:   {args.oidc_contract}")

    contract = _read_yaml(args.oidc_contract)
    authelia = _read_yaml(args.authelia_config)
    if contract is None or authelia is None:
        return 1

    contract_clients = contract.get("clients") or []
    authelia_clients = (
        ((authelia.get("identity_providers") or {})
         .get("oidc") or {})
        .get("clients", []) or []
    )

    results = [
        check_pbkdf2_roundtrip(
            contract_clients, authelia_clients, args.client,
        ),
        check_token_endpoint_auth_method(
            authelia_clients, args.client,
        ),
        check_redirect_uris_have_no_typos(
            authelia_clients, args.client,
        ),
    ]
    if args.client == "jellyseerr":
        results.append(check_jellyseerr_plaintext_matches_contract(
            contract_clients, args.jellyseerr_settings,
        ))

    print()
    if all(results):
        print("[OK] Every check passed.")
        return 0
    print("[FAIL] One or more checks failed — see above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
