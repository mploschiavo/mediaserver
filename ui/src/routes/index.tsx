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
import { useOnboarding } from "@/features/onboarding/hooks";
import { PageHeader } from "@/components/layout/PageHeader";
import { Route as RootRoute } from "@/routes/__root";

/**
 * Landing route. The dashboard is being rebuilt one tab at a time;
 * the page renders the onboarding checklist + pre-upgrade migration
 * safety check when the controller surfaces them, and otherwise
 * redirects to /media-integrity (the showcase tab) so existing
 * deep-links keep their fallback.
 *
 * The decision is component-local rather than `beforeLoad` so we can
 * gate on Tanstack Query results — the redirect only fires once
 * neither hook has anything to say.
 */
function HomePage() {
  const reduce = useReducedMotion();
  const onboarding = useOnboarding();
  const migration = useValidateMigration();

  // While either probe is in flight, render nothing. The fallback
  // redirect mustn't fire until we've heard back, otherwise an admin
  // with onboarding still pending would get bounced to /media-integrity
  // before we know.
  if (onboarding.isLoading || migration.isLoading) {
    return null;
  }

  const showOnboarding = onboardingHasContent(onboarding.data);
  const showMigration = migrationCheckHasContent(migration.data);

  // Existing redirect-to-/media-integrity fallback: if neither card
  // has content (and neither hook errored into something visible),
  // we keep the historical behaviour.
  if (!showOnboarding && !showMigration) {
    return <Navigate to="/media-integrity" replace />;
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
