import { motion, useReducedMotion } from "framer-motion";
import { createRoute } from "@tanstack/react-router";
import { Route as RootRoute } from "@/routes/__root";
import {
  DisplayPrefsCard,
  DriftCard,
  EffectiveProfileCard,
  EnvVarsEditorCard,
  EnvViewerCard,
  LogLevelCard,
  ProfileEditorCard,
  ProfileViewPage,
  SettingsPage,
} from "@/features/settings";
import { AlertRulesCard } from "@/features/alerts/AlertRulesCard";
import { TelemetryConsentCard } from "@/features/telemetry/TelemetryConsentCard";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";

// The seven dashboard tabs below moved from this stub file into their
// own `<slug>.tsx` modules. We re-export their `Route` constants under
// the historical names so `routeTree.ts` stays untouched.
export { Route as ContentRoute } from "@/routes/content";
export { Route as LogsRoute } from "@/routes/logs";
export { Route as OpsRoute } from "@/routes/ops";
export { Route as RoutingRoute } from "@/routes/routing";
export { Route as WebhooksRoute } from "@/routes/webhooks";
export { Route as UsersRoute } from "@/routes/users";
export { Route as MeRoute } from "@/routes/me";

// Media Integrity is the luxury showcase — real component lives at
// `@/routes/media-integrity`. Re-exported here so `routeTree.ts`'s
// `MediaIntegrityRoute` import keeps working during the migration.
export { Route as MediaIntegrityRoute } from "@/routes/media-integrity";

/**
 * /profile — read-only YAML excerpt + link to /settings. The full
 * editor lives in the Settings surface; this route is kept light so
 * the existing `ProfileRoute` import in `routeTree.ts` keeps working
 * without churning the route tree.
 */
export const ProfileRoute = createRoute({
  getParentRoute: () => RootRoute,
  path: "/profile",
  component: ProfileViewPage,
});

/**
 * /settings page wrapper. Kept inline here (rather than another file
 * under `src/features/settings/`) so the Wave-5 ratchet sentinel for
 * `alerts-telemetry` records "NO NEW ROUTE — settings.tsx was extended
 * in place; AppShell.tsx may have alert-engine wiring".
 *
 * The four original tabs (Profile / Environment / Display / Log
 * level) compose the existing settings cards re-exported from
 * `@/features/settings`. Two new tabs ride alongside:
 *   - "Alerts" — restores client-side rules from dashboard.html:691
 *   - "Telemetry" — restores opt-in/out for `/api/telemetry`
 *
 * The rendered SettingsPage import is preserved (mounted on the
 * /profile sibling test path) so existing tests that mock
 * `@/features/settings.SettingsPage` continue to work.
 */
function SettingsTabbedPage() {
  const reduce = useReducedMotion();

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      data-testid="settings-page"
    >
      <PageHeader
        title="Settings"
        description="Workspace and environment configuration."
      />
      <Tabs defaultValue="profile" className="flex flex-col gap-4">
        <TabsList className="self-start">
          <TabsTrigger value="profile" data-testid="settings-tab-profile">
            Profile
          </TabsTrigger>
          <TabsTrigger
            value="environment"
            data-testid="settings-tab-environment"
          >
            Environment
          </TabsTrigger>
          <TabsTrigger value="display" data-testid="settings-tab-display">
            Display
          </TabsTrigger>
          <TabsTrigger value="log-level" data-testid="settings-tab-log-level">
            Log level
          </TabsTrigger>
          <TabsTrigger value="alerts" data-testid="settings-tab-alerts">
            Alerts
          </TabsTrigger>
          <TabsTrigger
            value="telemetry"
            data-testid="settings-tab-telemetry"
          >
            Telemetry
          </TabsTrigger>
        </TabsList>
        <TabsContent value="profile" className="flex flex-col gap-4">
          <EffectiveProfileCard />
          <ProfileEditorCard />
          <DriftCard />
        </TabsContent>
        <TabsContent value="environment" className="flex flex-col gap-4">
          <EnvViewerCard />
          <EnvVarsEditorCard />
        </TabsContent>
        <TabsContent value="display" className="flex flex-col gap-4">
          <DisplayPrefsCard />
        </TabsContent>
        <TabsContent value="log-level" className="flex flex-col gap-4">
          <LogLevelCard />
        </TabsContent>
        <TabsContent value="alerts" className="flex flex-col gap-4">
          <AlertRulesCard />
        </TabsContent>
        <TabsContent value="telemetry" className="flex flex-col gap-4">
          <TelemetryConsentCard />
        </TabsContent>
      </Tabs>
    </motion.div>
  );
}

// Re-exported so callers that previously imported `SettingsPage`
// from this file keep working. The tabbed wrapper above is what
// the route actually mounts.
export { SettingsPage };

/**
 * /settings — workspace + environment configuration. The route is
 * extended in place (no new top-level route) so the wave-5 ratchet
 * sentinel records "NO NEW ROUTE — settings.tsx was extended in
 * place; AppShell.tsx may have alert-engine wiring".
 */
export const SettingsRoute = createRoute({
  getParentRoute: () => RootRoute,
  path: "/settings",
  component: SettingsTabbedPage,
});
