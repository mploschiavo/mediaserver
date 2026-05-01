import { Navigate, createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import {
  MigrationCheckCard,
  migrationCheckHasContent,
} from "@/features/stack-lifecycle/MigrationCheckCard";
import { useValidateMigration } from "@/features/stack-lifecycle/hooks";
import {
  OnboardingChecklist,
  onboardingHasContent,
} from "@/features/onboarding/OnboardingChecklist";
import { QuickStartCards } from "@/features/onboarding/QuickStartCards";
import { useOnboarding } from "@/features/onboarding/hooks";
import { PageHeader } from "@/components/layout/PageHeader";
import { Route as RootRoute } from "@/routes/__root";

const QUICK_START_PROGRESS_THRESHOLD = 80;

/**
 * Landing route. Renders the onboarding checklist + pre-upgrade
 * migration safety check when the controller surfaces them, and
 * otherwise lands on /ops — the operator dashboard.
 *
 * `BootstrapProgressBanner` is mounted at chrome level in
 * `AppShell`, so this page does not re-mount it.
 */
function HomePage() {
  const reduce = useReducedMotion();
  const onboarding = useOnboarding();
  const migration = useValidateMigration();

  if (onboarding.isLoading || migration.isLoading) {
    return null;
  }

  const showOnboarding = onboardingHasContent(onboarding.data);
  const showMigration = migrationCheckHasContent(migration.data);

  if (!showOnboarding && !showMigration) {
    return <Navigate to="/ops" replace />;
  }

  // Quick-start CTAs only earn the dashboard's attention once the
  // system is largely ready. Below the threshold the hero card +
  // checklist tell the story; piling "what to do next" on top of
  // an in-progress setup feels noisy.
  const quickStartReady =
    !!onboarding.data &&
    typeof onboarding.data.progress_pct === "number" &&
    onboarding.data.progress_pct >= QUICK_START_PROGRESS_THRESHOLD;

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      data-testid="home-page"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
    >
      <PageHeader
        title="Welcome"
        description="Three steps to a working stack — everything else is optional."
      />
      {showOnboarding ? <OnboardingChecklist /> : null}
      {quickStartReady ? <QuickStartCards /> : null}
      {showMigration ? <MigrationCheckCard /> : null}
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/",
  component: HomePage,
});
