import {
  Boxes,
  Compass,
  Download,
  Library,
  Radar,
  Sparkles,
} from "lucide-react";
import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { LibrariesTable } from "@/features/library/LibrariesTable";
import { LibraryAdditionsChart } from "@/features/library/LibraryAdditionsChart";
import { LibraryDataSourceBanner } from "@/features/library/LibraryDataSourceBanner";
import { LibraryStatsTiles } from "@/features/library/LibraryStatsTiles";
import { RecentAdditionsCard } from "@/features/library/RecentAdditionsCard";
import { IndexersTable } from "@/features/indexers/IndexersTable";
import { QualityProfilesCard } from "@/features/quality-profiles/QualityProfilesCard";
import { DiscoveryListsCard } from "@/features/discovery/DiscoveryListsCard";
import { ImportListsCard } from "@/features/discovery/ImportListsCard";
import { CustomServiceCard } from "@/features/custom-services/CustomServiceCard";
import { CustomFormatsCard } from "@/features/custom-formats/CustomFormatsCard";
import { ActiveDownloadsTable } from "@/features/downloads/ActiveDownloadsTable";
import { DownloadAnalyticsCard } from "@/features/downloads/DownloadAnalyticsCard";
import { DownloadHistoryTable } from "@/features/downloads/DownloadHistoryTable";
import { Route as RootRoute } from "@/routes/__root";

const TABS = [
  { value: "library", label: "Library", icon: Library },
  { value: "indexers", label: "Indexers", icon: Radar },
  { value: "quality", label: "Quality", icon: Sparkles },
  { value: "discovery", label: "Discovery", icon: Compass },
  { value: "custom", label: "Custom", icon: Boxes },
  { value: "downloads", label: "Downloads", icon: Download },
] as const;

function ContentPage() {
  const reduce = useReducedMotion();

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Content"
        description="Library, indexers, quality, discovery, custom, and downloads."
      />

      <LibraryStatsTiles />

      {/* The defaults-vs-live banner sits above the Tabs so it's the
          first thing the operator sees when their library counts are
          stale. The banner self-trims (returns null) unless the
          `defaults` source + `jellyfin reachable` predicate matches. */}
      <LibraryDataSourceBanner />

      <Tabs defaultValue="library">
        <TabsList data-testid="content-tabs">
          {TABS.map((t) => (
            <TabsTrigger
              key={t.value}
              value={t.value}
              data-testid={`content-tab-${t.value}`}
            >
              <t.icon className="mr-1 size-3.5" aria-hidden />
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent
          value="library"
          className="flex flex-col gap-4"
          data-testid="content-panel-library"
        >
          <LibraryAdditionsChart />
          <LibrariesTable />
          <RecentAdditionsCard />
        </TabsContent>

        <TabsContent
          value="indexers"
          data-testid="content-panel-indexers"
        >
          <IndexersTable />
        </TabsContent>

        <TabsContent
          value="quality"
          data-testid="content-panel-quality"
        >
          <QualityProfilesCard />
        </TabsContent>

        <TabsContent
          value="discovery"
          className="flex flex-col gap-4"
          data-testid="content-panel-discovery"
        >
          <ImportListsCard />
          <DiscoveryListsCard />
        </TabsContent>

        <TabsContent
          value="custom"
          className="flex flex-col gap-4"
          data-testid="content-panel-custom"
        >
          <CustomServiceCard />
          <CustomFormatsCard />
        </TabsContent>

        <TabsContent
          value="downloads"
          className="flex flex-col gap-4"
          data-testid="content-panel-downloads"
        >
          <ActiveDownloadsTable />
          <DownloadAnalyticsCard />
          <DownloadHistoryTable />
        </TabsContent>
      </Tabs>
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/content",
  component: ContentPage,
});
