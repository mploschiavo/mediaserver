# Real config fixtures

These are **sanitised snapshots of actual config files** captured from running services in the compose stack. They exist because synthesised test fixtures kept missing the shapes that real services produce — most recently the SABnzbd `__version__ = 19` preamble line that broke our INI probe and made the dashboard show "Config Corrupt" on a perfectly healthy install.

## How the integrity probe consumes them

[`tests/unit/test_real_config_fixtures.py`](../../test_real_config_fixtures.py) walks every file in this tree, runs it through `ConfigIntegrityService`, and asserts:

- Files under `<service>/` must report `status="ok"` (or `status="invalid"` for `authelia/configuration.yml` — semantic validation runs there too).
- Files under `corrupt/` must report `status="corrupt"` (parse failure) or `status="invalid"` (semantic failure). Each corrupt fixture is named for the *failure shape* it represents.

## How to add a fixture

When you find a real config file that the probe gets wrong:

1. **Capture from a running container.** Don't paste from memory; the failures we miss are exactly the shapes nobody remembers.
   ```bash
   docker exec sabnzbd cat /config/sabnzbd.ini > tests/unit/fixtures/configs/sabnzbd/sabnzbd.ini
   ```
2. **Sanitise every secret.** Replace API keys, passwords, JWT secrets, vapid keys, OAuth client secrets with the literal string `REDACTED_<KIND>` (e.g. `REDACTED_API_KEY_HEX`, `REDACTED_PASSWORD`). Keep the *length and shape* roughly the same so any length-aware validator still fires.
   - Quick check before commit: `grep -rE '[a-f0-9]{20,}|password\s*=\s*[^"]' tests/unit/fixtures/configs/` should return only `REDACTED_*` matches.
3. **Add a test case** for the *bug pattern* this fixture represents. The fixture itself isn't the test — the test that's named after the bug is. Example: `test_sabnzbd_preamble_before_first_section`.

## Why this directory exists

Every fixture in `corrupt/` has a comment at the top explaining the production failure it pins. Every fixture in `<service>/` was captured from a healthy live install. If a fixture stops parsing after a probe refactor, **don't update the fixture to match the probe** — fix the probe (or, if the upstream service genuinely changed format, update the fixture and bump the version comment).

## Coverage matrix

| Service     | Format | Fixture                              | Bug it pins (or "healthy baseline")              |
|-------------|--------|--------------------------------------|--------------------------------------------------|
| sabnzbd     | ini    | `sabnzbd/sabnzbd.ini`                | `__version__` preamble before first `[section]`  |
| prowlarr    | xml    | `prowlarr/config.xml`                | healthy baseline (also pinned: trailing-junk twin) |
| sonarr      | xml    | `sonarr/config.xml`                  | healthy baseline                                  |
| bazarr      | yaml   | `bazarr/config/config.yaml`          | healthy baseline                                  |
| jellyseerr  | json   | `jellyseerr/settings.json`           | healthy baseline                                  |
| tautulli    | ini    | `tautulli/config.ini`                | healthy baseline                                  |
| authelia    | yaml   | `authelia/configuration.yml`         | healthy baseline (semantic validator must pass)   |
| (corrupt)   | xml    | `corrupt/prowlarr_trailing_junk.xml` | 2026-04-20 Prowlarr `</Config>sm>\n</Config>`    |
| (corrupt)   | ini    | `corrupt/sabnzbd_unclosed_section.ini` | unclosed `[section`                            |
| (corrupt)   | json   | `corrupt/jellyseerr_truncated.json`  | truncated mid-string                              |
| (corrupt)   | yaml   | `corrupt/bazarr_unclosed_quote.yaml` | unterminated quoted string                        |
| (corrupt)   | yaml   | `corrupt/authelia_bare_cookie_domain.yml` | semantic: cookie domain has no period         |
