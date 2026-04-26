import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { ProfileEditorCard } from "./ProfileEditorCard";
import { EffectiveProfileCard } from "./EffectiveProfileCard";
import { DriftCard } from "./DriftCard";
import { EnvViewerCard } from "./EnvViewerCard";
import { EnvVarsEditorCard } from "./EnvVarsEditorCard";
import { DisplayPrefsCard } from "./DisplayPrefsCard";
import { LogLevelCard } from "./LogLevelCard";
import { MetadataPreferencesCard } from "./MetadataPreferencesCard";
import { SponsorCard } from "./SponsorCard";

/**
 * /settings — workspace + environment configuration. Profile YAML
 * editing, drift, env (read + write), display prefs, log level.
 * Each tab is a thin composition of feature cards; data fetching
 * lives inside the cards themselves.
 */
export function SettingsPage() {
  const reduce = useReducedMotion();

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      data-testid="settings-page"
    >
      <PageHeader
        title="Settings"
        description="Workspace and environment configuration."
      />
      <Tabs defaultValue="profile" className="flex flex-col gap-4">
        <TabsList className="self-start">
          <TabsTrigger value="profile" data-testid="settings-tab-profile">
            Profile
          </TabsTrigger>
          <TabsTrigger
            value="environment"
            data-testid="settings-tab-environment"
          >
            Environment
          </TabsTrigger>
          <TabsTrigger value="display" data-testid="settings-tab-display">
            Display
          </TabsTrigger>
          <TabsTrigger value="log-level" data-testid="settings-tab-log-level">
            Log level
          </TabsTrigger>
          <TabsTrigger value="about" data-testid="settings-tab-about">
            About
          </TabsTrigger>
        </TabsList>
        <TabsContent value="profile" className="flex flex-col gap-4">
          <EffectiveProfileCard />
          <ProfileEditorCard />
          <DriftCard />
        </TabsContent>
        <TabsContent value="environment" className="flex flex-col gap-4">
          <EnvViewerCard />
          <EnvVarsEditorCard />
        </TabsContent>
        <TabsContent value="display" className="flex flex-col gap-4">
          <MetadataPreferencesCard />
          <DisplayPrefsCard />
        </TabsContent>
        <TabsContent value="log-level" className="flex flex-col gap-4">
          <LogLevelCard />
        </TabsContent>
        <TabsContent value="about" className="flex flex-col gap-4">
          <SponsorCard />
        </TabsContent>
      </Tabs>
    </motion.div>
  );
}
