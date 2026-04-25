import { createRoute, useLocation } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { Route as RootRoute } from "@/routes/__root";
import { GuardrailsPage } from "@/features/guardrails";

interface GuardrailsSearch {
  /** Optional rule id to focus and auto-select its domain tab.
   *  Wired by ``TriggeredBanner`` so a click jumps straight to the
   *  offending rule. */
  focus?: string;
}

/**
 * /guardrails — cross-domain guardrail registry. Ownership of the
 * outer page shell + PageHeader stays here; the actual composition
 * (tabs, rows, threshold inputs) lives in the feature folder.
 *
 * We read the focus param via ``useLocation`` (matching the
 * AuditLogTable pattern) so the hook works under any route during
 * tests without a strict ``from:`` arg to ``useSearch``.
 */
function GuardrailsRouteComponent() {
  const reduce = useReducedMotion();
  const location = useLocation();
  const search = (location.search ?? {}) as Record<string, unknown>;
  const rawFocus = search?.focus;
  const focus = typeof rawFocus === "string" && rawFocus.length > 0 ? rawFocus : null;
  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      data-testid="guardrails-page"
    >
      <PageHeader
        title="Guardrails"
        description="Cross-domain rules with thresholds, evaluation history, and remediation hooks."
      />
      <GuardrailsPage focusedId={focus} />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/guardrails",
  component: GuardrailsRouteComponent,
  validateSearch: (raw): GuardrailsSearch => {
    const focus = (raw as { focus?: unknown })?.focus;
    return {
      focus: typeof focus === "string" ? focus : undefined,
    };
  },
});
