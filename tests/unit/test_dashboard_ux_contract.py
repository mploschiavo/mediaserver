"""UX contract tests for the dashboard.

These tests pin user-visible promises by pattern-matching the
generated ``dashboard.html``. The dashboard is a static file
served as-is to the browser, so we can read it as a string and
assert that the JS branches we care about exist (and that
removed bug patterns stay removed).

Coverage anchors to specific real bugs the user reported on
2026-04-20:

- **A: SSO mode Login column** — when ``_authMode`` is
  ``authelia``/``authentik``, the Login column shouldn't say
  "Error" because the gateway handles login. Pin the branch in
  ``renderServices`` that emits "SSO" instead.
- **B: Re-check Health button** — the old "Check All Logins"
  button only revalidated credentials. The new wider scope
  covers HTTP health, integrity, crashloops, stories.
- **C: Trend column header** — sparklines moved out of the
  Service-name cell into their own column so the name stops
  jittering as new probes arrive.
- **D: Access-your-services filter** — only user-facing apps
  (media server + request UI). Indexers/arrs/downloaders are
  admin surface.
- **E: No "media server app" generic copy** — the wizard must
  name the actual media server (Jellyfin/Plex/Emby).
- **F: Routing matrix filters to enabled services** — pinned via
  the helper ``_enabledSvcs()`` that wraps the SVCS filter.

If any of these regress (someone removes the SSO branch, or
re-introduces "media server app" copy), this file fails fast."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

DASHBOARD = (
    ROOT / "src" / "media_stack" / "api" / "dashboard.html"
).read_text(encoding="utf-8")


class SsoModeLoginColumnTests(unittest.TestCase):
    """A: under SSO the Login column must not show Error."""

    def test_sso_branch_present_in_render_services(self) -> None:
        """Pin the source-of-truth: there must be a code branch
        keyed off ``_authMode==='authelia'``/``'authentik'`` that
        sets a non-error label for SSO services."""
        # Walk to the renderServices function body.
        idx = DASHBOARD.find("function renderServices(")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 6000]
        # The branch must mention authelia + authentik AND emit
        # an "SSO" label (or class) — not just bypass.
        self.assertIn("'authelia'", body)
        self.assertIn("'authentik'", body)
        self.assertIn("'SSO'", body,
                      "renderServices should emit 'SSO' as the Login "
                      "label when the gateway handles auth.")

    def test_login_does_not_say_error_for_protected_sso_service(self) -> None:
        """Source-level pinning: the branch that converts native
        login probe results into the Login column must be guarded
        by the SSO check, so a protected service never gets an
        ``error`` class while SSO is active."""
        # The fix added ssoActive + early-return-style branch.
        self.assertIn("ssoActive", DASHBOARD)


class ReCheckHealthButtonTests(unittest.TestCase):
    """B: button rename + wider scope."""

    def test_button_label_is_recheck_health_not_check_all_logins(self) -> None:
        self.assertNotIn(
            ">Check All Logins<", DASHBOARD,
            "The 'Check All Logins' label was renamed to 'Re-check "
            "Health' to reflect its broader scope.",
        )
        self.assertIn(">Re-check Health<", DASHBOARD)

    def test_recheck_calls_probeServices_and_revalidate(self) -> None:
        """The handler must invoke both probeServices() (which
        covers /api/health, /api/health/config-integrity,
        /api/health/stories) and revalidateCredentials()."""
        idx = DASHBOARD.find("function recheckAllHealth")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 600]
        self.assertIn("probeServices", body)
        self.assertIn("revalidateCredentials", body)


class TrendColumnTests(unittest.TestCase):
    """C: sparkline moved into its own column."""

    def test_trend_column_in_table_header(self) -> None:
        self.assertIn(">Trend<", DASHBOARD)

    def test_sparkline_not_inside_service_name_cell(self) -> None:
        """Pin the layout: ``s.name+sparkHtml(`` was the old
        inline pattern. The new layout has them in separate
        ``<td>`` elements."""
        # Find the row-render section.
        idx = DASHBOARD.find("function renderServices(")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 6000]
        self.assertNotIn(
            "s.name+sparkHtml(s.id)",
            body,
            "Sparkline still concatenated to service name — must "
            "live in its own <td> column.",
        )


class NonAdminUserStepTests(unittest.TestCase):
    """The wizard's first step must promote a non-admin user for
    daily use. Reusing the bootstrap admin for streaming/requesting
    is the same anti-pattern as logging into a home PC as root —
    any compromised browser extension or web app inherits admin's
    full permission set on every service."""

    def test_wizard_has_non_admin_user_step(self) -> None:
        idx = DASHBOARD.find("function renderWizardSteps")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 6000]
        self.assertIn(
            "Set Up a Non-Admin User", body,
            "Wizard missing the 'Set Up a Non-Admin User' step. "
            "Daily use should not be done as the bootstrap admin.",
        )

    def test_non_admin_step_links_to_users_tab(self) -> None:
        """A button or link must take the user straight to the
        Users tab where create-user lives. Telling them to find it
        themselves is the kind of friction that gets skipped."""
        idx = DASHBOARD.find("Set Up a Non-Admin User")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 1500]
        self.assertIn(
            "tab-users", body,
            "Non-admin-user step must include a link/button to "
            "the Users tab — otherwise users have to hunt for "
            "where to create the account.",
        )

    def test_cert_step_mentions_internet_exposure_path(self) -> None:
        """Pins: the Trust-the-Stack-Certificate step includes a
        collapsed note explaining when a self-signed cert is NOT
        appropriate (internet-facing) and how to upload a real cert.
        Without this, users who expose the stack publicly either
        ship the self-signed cert to the world (training everyone
        to click through warnings) or don't know the upload path."""
        idx = DASHBOARD.find("Trust the Stack Certificate")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 4000]
        self.assertIn(
            "Exposing this stack on the internet", body,
            "Cert step must include an internet-exposure block "
            "that explains the limits of self-signed certs and "
            "points at the TLS upload flow.",
        )
        self.assertIn(
            "tls-cert-details", body,
            "Internet-exposure block must link to the TLS upload "
            "UI at Routing > TLS Certificate (scrollIntoView on "
            "#tls-cert-details), not just describe it.",
        )

    def test_open_users_tab_link_scrolls_into_view(self) -> None:
        """The 'Open Users tab' link is rendered halfway down the
        page in the wizard. ``showTab`` deliberately suppresses
        scroll-jump (good for keyboard tab navigation), so the
        click LOOKS like it does nothing. The wizard's link must
        explicitly scroll the tab content into view after switching.
        Pin the behavior."""
        idx = DASHBOARD.find("Set Up a Non-Admin User")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 1500]
        self.assertIn(
            "scrollIntoView", body,
            "'Open Users tab' link doesn't scroll the tab into "
            "view; user clicks it from the wizard and sees nothing "
            "change. Add a scrollIntoView call after showTab.",
        )

    def test_connect_tv_step_does_not_default_to_admin(self) -> None:
        """The Connect-Your-TV instructions must NOT just say
        'sign in with admin' — the prior step recommended
        creating a non-admin user, this one should reference it."""
        idx = DASHBOARD.find("Connect Your TV")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 1500]
        self.assertIn(
            "non-admin", body,
            "Connect-Your-TV instructions still default to admin "
            "without mentioning the non-admin user we just told "
            "them to create.",
        )


class AccessYourServicesFilterTests(unittest.TestCase):
    """D: only user-facing apps in the wizard's Step 1."""

    def test_step1_filters_to_user_facing_ids(self) -> None:
        idx = DASHBOARD.find("// Access your services")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 1500]
        # The filter must include jellyfin/plex/jellyseerr at
        # minimum and not iterate the whole SVCS list.
        self.assertIn("userFacingIds", body)
        self.assertIn("'jellyfin'", body)
        self.assertIn("'jellyseerr'", body)

    def test_step1_handles_zero_user_facing(self) -> None:
        """If no user-facing apps are enabled, the wizard should
        explain that, not just render an empty button row."""
        idx = DASHBOARD.find("// Access your services")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 1500]
        self.assertIn("No user-facing apps enabled", body)


class MediaServerCopyTests(unittest.TestCase):
    """E: no generic 'media server' copy in the wizard text."""

    def test_no_generic_media_server_app_copy(self) -> None:
        """The wizard's Connect-Your-TV step used to say
        "Install the media server app" — replace with the resolved
        name (Jellyfin/Plex/Emby)."""
        self.assertNotIn(
            "Install the media server app",
            DASHBOARD,
            "Generic 'media server app' copy is ambiguous — name "
            "the actual app via the mediaName resolver.",
        )

    def test_wizard_resolves_media_server_name(self) -> None:
        """The fix introduced ``mediaSvc`` / ``mediaName`` so the
        copy reads 'Jellyfin' (or whichever app is actually
        deployed)."""
        idx = DASHBOARD.find("function renderWizardSteps")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 4000]
        self.assertIn("mediaName", body)

    def test_wizard_uses_profile_bindings_not_registry_find(self) -> None:
        """The wizard must source the media-server choice from
        ``_techBindings.media_server`` (the profile's binding),
        not by ``find()``-ing the SVCS registry. The bug the user
        reported on 2026-04-20: ``emby.yaml`` exists in
        ``contracts/services`` even on a Jellyfin-only stack, so
        a ``SVCS.find(x => x.id in [jellyfin, plex, emby])`` walk
        could return the emby entry first and the wizard rendered
        'Connect to Emby' on a stack that doesn't have Emby."""
        idx = DASHBOARD.find("function renderWizardSteps")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 4000]
        self.assertIn(
            "_techBindings", body,
            "Wizard must read the deployed media server from "
            "_techBindings.media_server (loaded from /api/profile), "
            "not guess by iterating SVCS.",
        )
        # Pin the absence of the buggy pattern.
        self.assertNotIn(
            "SVCS.find(x=>x.id==='jellyfin'||x.id==='plex'||x.id==='emby')",
            body,
            "Old find()-by-id-list pattern reintroduced — see "
            "docstring above.",
        )


class RoutingMatrixEnabledFilterTests(unittest.TestCase):
    """F: routing matrix + DNS host list filter to enabled
    services so the user doesn't see broken hostnames for stuff
    they didn't deploy."""

    def test_routing_uses_enabled_filter_helper(self) -> None:
        self.assertIn(
            "function _enabledSvcs",
            DASHBOARD,
            "Expected a helper that filters SVCS to ones the user "
            "actually enabled — same filter the services table "
            "applies in 'hideDisabled' mode.",
        )

    def test_render_matrix_iterates_enabled_only(self) -> None:
        idx = DASHBOARD.find("function renderMatrix")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 1200]
        self.assertIn("_enabledSvcs()", body,
                      "renderMatrix must iterate only enabled "
                      "services.")
        self.assertNotIn(
            "for(const s of SVCS)", body,
            "Direct SVCS iteration includes disabled services — "
            "use _enabledSvcs() instead.",
        )

    def test_build_dns_uses_enabled_filter(self) -> None:
        idx = DASHBOARD.find("function buildDns")
        self.assertGreater(idx, -1)
        body = DASHBOARD[idx:idx + 800]
        self.assertIn("_enabledSvcs()", body,
                      "DNS host list must include only enabled "
                      "services so /etc/hosts doesn't get cluttered "
                      "with names that resolve to nothing.")


if __name__ == "__main__":
    unittest.main()
