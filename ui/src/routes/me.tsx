import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  LoginHistoryCard,
  MfaCard,
  ProfileCard,
  SessionsCard,
  TokensCard,
} from "@/features/me";
import { ChangePasswordCard } from "@/features/me/ChangePasswordCard";
import { Route as RootRoute } from "@/routes/__root";

/**
 * /me — operator's self-service surface. Each card reads its own
 * data from the backend via hooks in `@/features/me`. The route is
 * a pure composition — no data-fetching lives here.
 */
function MePage() {
  const reduce = useReducedMotion();

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      data-testid="me-page"
    >
      <PageHeader
        title="My profile"
        description="Your account and security."
      />

      <ProfileCard />

      <ChangePasswordCard />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <SessionsCard />
        <TokensCard />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <MfaCard />
        <LoginHistoryCard />
      </div>
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/me",
  component: MePage,
});
