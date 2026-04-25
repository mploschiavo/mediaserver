import { ConcurrentSpikesCard } from "./ConcurrentSpikesCard";
import { FailedLoginsCard } from "./FailedLoginsCard";
import { NewLocationsCard } from "./NewLocationsCard";

/**
 * Composes the three abuse-defence security signals into one
 * vertically-stacked operator surface. The cards stay full-width
 * on every breakpoint — the data sets aren't comparable, so a
 * side-by-side desktop layout would force the eye to bounce
 * between three unrelated tables. The route file owns the outer
 * `max-w-6xl` page-shell + PageHeader + entrance animation; this
 * component only paints the in-column composition.
 */
export function SecurityPage() {
  return (
    <div className="flex flex-col gap-6" data-testid="security-page">
      <FailedLoginsCard />
      <NewLocationsCard />
      <ConcurrentSpikesCard />
    </div>
  );
}
