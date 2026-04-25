import { useState } from "react";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  QUALITY_SERVICES,
  readProfiles,
  useQualityProfiles,
  useToggleQualityProfile,
  type QualityProfileEntry,
  type QualityService,
} from "./hooks";

interface ProfileRowProps {
  service: QualityService;
  profile: QualityProfileEntry;
}

function ProfileRow({ service, profile }: ProfileRowProps) {
  const toggle = useToggleQualityProfile();
  const [optimistic, setOptimistic] = useState<boolean | null>(null);

  const enabled =
    optimistic ??
    (profile.enabled === undefined && profile.active === undefined
      ? true
      : (profile.enabled ?? profile.active ?? false));

  const profileId =
    typeof profile.id === "number" ? profile.id : undefined;

  const onToggle = (next: boolean) => {
    if (profileId === undefined) {
      toast.error("Profile id missing — cannot toggle");
      return;
    }
    setOptimistic(next);
    toggle.mutate(
      { service, profileId, enabled: next },
      {
        onSuccess: () =>
          toast.success(
            `${profile.name ?? "Profile"} ${next ? "enabled" : "disabled"}`,
          ),
        onError: (err) => {
          setOptimistic(null);
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Toggle failed";
          toast.error(msg);
        },
      },
    );
  };

  const name =
    typeof profile.name === "string" && profile.name
      ? profile.name
      : `Profile ${profileId ?? "?"}`;

  return (
    <li
      className="flex items-center justify-between gap-3 py-2 text-sm"
      data-testid={`quality-profile-${service}-${profileId ?? "unknown"}`}
    >
      <div className="flex flex-col">
        <span className="font-medium text-fg">{name}</span>
        {profileId !== undefined ? (
          <span className="font-mono text-xs text-fg-faint">
            id {profileId}
          </span>
        ) : null}
      </div>
      <Switch
        checked={enabled}
        onCheckedChange={onToggle}
        disabled={toggle.isPending || profileId === undefined}
        aria-label={`${name} enabled`}
        data-testid={`quality-toggle-${service}-${profileId ?? "unknown"}`}
      />
    </li>
  );
}

interface ServicePanelProps {
  service: QualityService;
}

function ServicePanel({ service }: ServicePanelProps) {
  const query = useQualityProfiles(service);
  const profiles = readProfiles(query.data);

  if (query.isLoading) {
    return (
      <div
        className="flex flex-col gap-2"
        data-testid={`quality-loading-${service}`}
      >
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }
  if (query.error) {
    return (
      <div
        role="alert"
        data-testid={`quality-error-${service}`}
        className="text-sm text-danger"
      >
        {query.error.message}
      </div>
    );
  }
  if (profiles.length === 0) {
    return (
      <EmptyState
        icon={Sparkles}
        title={`No quality profiles for ${service}`}
        description="Configure profiles in the upstream Servarr app to see them here."
      />
    );
  }
  return (
    <ul
      className="divide-y divide-border"
      role="list"
      data-testid={`quality-list-${service}`}
    >
      {profiles.map((p, i) => (
        <ProfileRow
          key={typeof p.id === "number" ? p.id : `idx-${i}`}
          service={service}
          profile={p}
        />
      ))}
    </ul>
  );
}

export function QualityProfilesCard() {
  return (
    <Card data-testid="quality-profiles-card">
      <CardHeader>
        <CardTitle>Quality profiles</CardTitle>
        <CardDescription>
          Per-service profile catalogue. Toggle a profile to enable it for
          new entries (existing items keep their assignment).
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="sonarr">
          <TabsList>
            {QUALITY_SERVICES.map((s) => (
              <TabsTrigger
                key={s}
                value={s}
                data-testid={`quality-tab-${s}`}
                className="capitalize"
              >
                {s}
                <Badge variant="outline" className="ml-2">
                  •
                </Badge>
              </TabsTrigger>
            ))}
          </TabsList>
          {QUALITY_SERVICES.map((s) => (
            <TabsContent key={s} value={s} className="mt-3">
              <ServicePanel service={s} />
            </TabsContent>
          ))}
        </Tabs>
      </CardContent>
    </Card>
  );
}
