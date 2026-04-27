import { LivetvSourcesCard } from "./LivetvSourcesCard";
import { IptvCountriesCard } from "./IptvCountriesCard";
import { EpgProvidersCard } from "./EpgProvidersCard";
import { EpgHealthCard } from "./EpgHealthCard";
import { LivetvHealthChart } from "./LivetvHealthChart";

/**
 * Composes the four Live TV / IPTV / EPG operator cards. The route
 * file owns the outer `max-w-6xl` page-shell + `PageHeader` so every
 * tab lines up width-for-width — this component only paints the
 * in-column composition.
 *
 * Order is intentional: sources are the primary configuration, the
 * country picker is a quick-add for the source list, EPG providers is
 * a browse-only catalog, and the health chip sits at the bottom as a
 * read-only status indicator.
 */
export function LivetvPage() {
  return (
    <div className="flex flex-col gap-6" data-testid="livetv-page">
      <LivetvHealthChart />
      <LivetvSourcesCard />
      <IptvCountriesCard />
      <EpgProvidersCard />
      <EpgHealthCard />
    </div>
  );
}
