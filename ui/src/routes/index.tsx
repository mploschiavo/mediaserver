import { Navigate, createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import {
  MigrationCheckCard,
  migrationCheckHasContent,
} from "@/features/stack-lifecycle/MigrationCheckCard";
import { useValidateMigration } from "@/features/stack-lifecycle/hooks";
import { BootstrapProgressBanner } from "@/features/onboarding/BootstrapProgressBanner";
import {
  OnboardingChecklist,
  onboardingHasContent,
} from "@/features/onboarding/OnboardingChecklist";
import { useOnboarding } from "@/features/onboarding/hooks";
import { PageHeader } from "@/components/layout/PageHeader";
import { Route as RootRoute } from "@/routes/__root";

/**
 * Landing route. The page renders the onboarding checklist +
 * pre-upgrade migration safety check when the controller surfaces
 * them, and otherwise lands on /ops — the operator dashboard.
 *
 * Why /ops: it's the broadest "what's the state of my stack right
 * now?" surface. The historical /media-integrity default surprised
 * operators ("why is the dashboard a single subsystem?"). /ops is
 * the natural homepage for an admin tool.
 *
 * The decision is component-local rather than `beforeLoad` so we
 * can gate on Tanstack Query results — the redirect only fires
 * once neither hook has anything to say.
 */
function HomePage() {
  const reduce = useReducedMotion();
  const onboarding = useOnboarding();
  const migration = useValidateMigration();

  // While either probe is in flight, render nothing. The fallback
  // redirect mustn't fire until we've heard back, otherwise an admin
  // with onboarding still pending would get bounced before we know.
  if (onboarding.isLoading || migration.isLoading) {
    return null;
  }

  const showOnboarding = onboardingHasContent(onboarding.data);
  const showMigration = migrationCheckHasContent(migration.data);

  // Default landing — /ops gives the operator the breadth they
  // expect from a "home" view (services, jobs, quick actions).
  if (!showOnboarding && !showMigration) {
    return <Navigate to="/ops" replace />;
  }

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
        description="Stack overview, onboarding progress, and pre-upgrade safety checks."
      />
      <BootstrapProgressBanner />
      {showOnboarding ? <OnboardingChecklist /> : null}
      {showMigration ? <MigrationCheckCard /> : null}
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/",
  component: HomePage,
});
