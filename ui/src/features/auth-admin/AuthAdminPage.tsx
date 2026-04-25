import { AuthModeCard } from "./AuthModeCard";
import { OidcProvidersCard } from "./OidcProvidersCard";
import { ServicePoliciesCard } from "./ServicePoliciesCard";

/**
 * Composes the three operator-facing auth-admin cards. Layout matches
 * the routing-admin / users-admin surfaces: a single-column stack of
 * Cards. The route file owns the outer `max-w-6xl` page-shell +
 * PageHeader + entrance animation; this component only paints the
 * in-column composition.
 *
 * Order is intentional: the auth mode (the global toggle) sits up
 * top, followed by the providers it depends on, and finally the
 * per-service policy table that's only meaningful once a non-`none`
 * mode is selected.
 */
export function AuthAdminPage() {
  return (
    <div className="flex flex-col gap-6" data-testid="auth-admin-page">
      <AuthModeCard />
      <OidcProvidersCard />
      <ServicePoliciesCard />
    </div>
  );
}
