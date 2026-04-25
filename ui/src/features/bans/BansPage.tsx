import { IpBansCard } from "./IpBansCard";
import { UserBansCard } from "./UserBansCard";

/**
 * Composes the user-ban and IP-ban cards into the Bans operator
 * surface. Stacked on every breakpoint — the data sets aren't
 * homogeneous, so a side-by-side desktop layout would force the
 * eye to bounce between two unrelated tables. The route file owns
 * the outer `max-w-6xl` page-shell + PageHeader + entrance
 * animation; this component only paints the in-column composition.
 */
export function BansPage() {
  return (
    <div className="flex flex-col gap-6" data-testid="bans-page">
      <UserBansCard />
      <IpBansCard />
    </div>
  );
}
