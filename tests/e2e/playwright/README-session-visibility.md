# Session-visibility e2e test suite

Playwright + axe-core + Lighthouse CI tests for the 5 new
session-visibility tabs.

## Files

| Path | Purpose |
|---|---|
| `tests/session-visibility.spec.ts` | UI shell smoke — each tab renders, forms have reason templates, emergency revoke requires phrase+reason, XSS runtime check |
| `tests/session-visibility-a11y.spec.ts` | axe-core WCAG 2.1 AA + best-practices checks on all 5 tabs + emergency dialog |
| `.lighthouserc.json` | Lighthouse CI config — perf/a11y/best-practices ≥ 95% gate per tab URL |
| `playwright.config.ts` | Extended with a new `session-visibility` project |

## Prerequisites

```
cd tests/e2e/playwright
npm install   # installs @playwright/test, @axe-core/playwright, axe-core, @lhci/cli
```

A running controller at `CONTROLLER_URL` (default `http://127.0.0.1:9100`)
is required. The controller should be started with:

```
STACK_ADMIN_USERNAME=admin STACK_ADMIN_PASSWORD=<pw> \
  .venv/bin/python3 -m media_stack.cli.commands.controller_serve \
  --port 9100
```

Tests skip unless `CONTROLLER_API_TEST=1`.

## Run

```
# UI shell smoke tests
CONTROLLER_API_TEST=1 npm run test:session-visibility

# Accessibility only (axe-core)
CONTROLLER_API_TEST=1 npm run test:a11y

# Lighthouse (performance + a11y + best-practices ≥ 95 per tab)
CONTROLLER_API_TEST=1 npm run test:lighthouse
```

## CI gate

Add to `.github/workflows/*.yml` after the controller starts in the
GH Action container:

```yaml
- name: Session-visibility e2e + a11y
  env:
    CONTROLLER_API_TEST: "1"
    CONTROLLER_URL: http://127.0.0.1:9100
  working-directory: tests/e2e/playwright
  run: |
    npm ci
    npx playwright install --with-deps chromium
    npx playwright test --project session-visibility
    npx lhci autorun
```

## What's checked

### session-visibility.spec.ts (7 tests)

- Every tab's script loads and exposes its `render*Tab` global.
- Security tab renders all 3 panels (failed logins, new locations,
  concurrent).
- Bans tab renders both forms with the full reason-template list.
- My Security tab renders all 4 sections.
- Emergency revoke confirmation gate works: phrase + reason both
  required; wrong phrase keeps the button disabled.
- Runtime XSS safety — a crafted `<script>` in a session username
  is rendered as text, not parsed. The `window.xssFired` canary
  stays undefined.

### session-visibility-a11y.spec.ts (7 tests)

- Each tab passes axe-core with `wcag2aa` + `wcag21aa` + best-practice
  tags and zero violations.
- Tested both empty-state and populated-state for tabs that render
  different markup based on data.
- Emergency revoke dialog (with `role="dialog"` / `aria-modal`) runs
  a second a11y pass to catch dialog-specific issues like missing
  focus trap.

### Lighthouse CI gate

Per-tab URL gates — **≥ 95%** on perf / a11y / best-practices
(SEO is a warning at 90% — admin UIs don't need indexing). Skipped
audits: `is-on-https` (dev targets are plain HTTP), `uses-http2`
(stdlib HTTPServer is HTTP/1), `redirects-http` (same reason).
Production gate should re-enable these once a TLS-terminating
Envoy sidecar is in front.
