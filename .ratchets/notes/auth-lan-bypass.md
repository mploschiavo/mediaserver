# Auth — restore LAN bypass as an opt-in

**Status:** TODO. Tracked from v1.0.176 (controller) when the unconditional LAN
bypass was removed. Operators currently challenged on every browser-session.

## Background

Pre-v1.0.176, the controller's `authelia_config_generator._build_access_control`
prepended this rule to Authelia's access_control list:

```yaml
- domain: ["*.<base_domain>"]
  networks: [192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12, 127.0.0.0/8]
  policy: bypass
```

Authelia evaluates rules top-to-bottom — first match wins — so any request from
an RFC-1918 / loopback client skipped the sign-in challenge entirely. The
controller's admin-fallback (`STACK_ADMIN_USERNAME`) then surfaced as the
identity, leading to "logged in as admin without ever signing in".

In v1.0.176 the bypass was removed so every browser session on the LAN gets
challenged once via SSO (the Authelia cookie is scoped to `base_domain`, so a
single login unlocks every `*.<base_domain>` host for the session lifetime —
no per-app re-prompt).

## Plan to make it opt-in

1. **Add a field** to `AutheliaOptions`:
   ```python
   lan_bypass: bool = False
   ```
   Default off so the v1.0.176+ behaviour holds for new deployments.
2. **Honor it in `_build_access_control`**: if `self._opts.lan_bypass` is true,
   prepend the historical bypass rule. The exact networks list lives in
   `pre-v1.0.176-bypass-rule.yaml` snippet for parity (see git history of
   `authelia_config_generator.py`).
3. **Surface it in the bootstrap profile** (`config/defaults/.../profile.yaml`):
   add a top-level `auth.lan_bypass: false` knob. Operators set it via
   `bootstrap-profile` overrides without touching the codebase.
4. **Document the trade-off** in `docs/auth.md`:
   - Off (default): one Authelia portal hit per session per LAN device. Aligns
     with the intuition that "the dashboard challenges me before I see admin
     surfaces". Stops casual LAN-side discovery (typo'd URL on a guest
     device, IoT device, smart TV).
   - On: zero-friction LAN access; the admin-fallback identity from
     `STACK_ADMIN_USERNAME` is what the UI sees. Acceptable on a single-
     operator homelab; not acceptable in any multi-tenant or shared LAN.
5. **Add a regression test** in `tests/auth/test_authelia_config_generator.py`:
   - Default options → no bypass rule in output.
   - `lan_bypass=True` → bypass rule present at index 0 with the four CIDRs.
6. **Roll out** behind a single controller release. No UI change required —
   the UI already surfaces the real Authelia identity in the TopBar
   (`useIdentity()` in `ui/src/api/hooks.ts`), so flipping the rule changes
   the displayed user without further wiring.

## Why deferred

- The opt-in default needs a deployment knob, which means touching the
  bootstrap profile schema + the operator-facing docs in the same change.
- The default behaviour the user wanted today (every LAN client signs in)
  is the controller's new built-in default; flipping back is a config knob
  for a future minor release, not a regression to fix.
