import { useMemo } from "react";
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Info,
  Sparkles,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  useHealthStories,
  type HealthStory,
  type HealthStorySeverity,
} from "./hooks";

const SEVERITY_RANK: Record<string, number> = {
  critical: 0,
  warn: 1,
  info: 2,
  ok: 3,
};

type SeverityVariant = "danger" | "warning" | "info" | "success" | "default";

function severityMeta(severity: string): {
  variant: SeverityVariant;
  icon: LucideIcon;
  label: string;
} {
  switch ((severity ?? "").toLowerCase() as HealthStorySeverity) {
    case "critical":
      return { variant: "danger", icon: AlertOctagon, label: "critical" };
    case "warn":
      return { variant: "warning", icon: AlertTriangle, label: "warn" };
    case "info":
      return { variant: "info", icon: Info, label: "info" };
    case "ok":
      return { variant: "success", icon: CheckCircle2, label: "ok" };
    default:
      return { variant: "default", icon: Info, label: severity || "unknown" };
  }
}

function isQuiet(stories: readonly HealthStory[]): boolean {
  // Treat "all stories ok" or "no stories at all" as quiet.
  if (stories.length === 0) return true;
  return stories.every(
    (s) => (s.severity ?? "").toLowerCase() === "ok",
  );
}

function StoryRow({ story }: { story: HealthStory }) {
  const meta = severityMeta(story.severity);
  return (
    <li
      className="flex flex-col gap-2 rounded-md border border-border bg-bg-1/40 p-3"
      data-testid={`story-${story.id}`}
    >
      <div className="flex items-start gap-3">
        <meta.icon
          aria-hidden
          className={
            meta.variant === "danger"
              ? "mt-0.5 size-4 shrink-0 text-danger"
              : meta.variant === "warning"
                ? "mt-0.5 size-4 shrink-0 text-warning"
                : meta.variant === "success"
                  ? "mt-0.5 size-4 shrink-0 text-success"
                  : "mt-0.5 size-4 shrink-0 text-fg-muted"
          }
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-fg">{story.headline}</span>
            <Badge variant={meta.variant}>{meta.label}</Badge>
          </div>
          {story.description ? (
            <p className="mt-1 text-sm text-fg-muted">{story.description}</p>
          ) : null}
          {story.affected_services && story.affected_services.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {story.affected_services.map((svc) => (
                <span
                  key={svc}
                  className="inline-flex items-center rounded-md border border-border bg-bg-2 px-1.5 py-0.5 font-mono text-xs text-fg-muted"
                >
                  {svc}
                </span>
              ))}
            </div>
          ) : null}
          {story.next_action ? (
            <a
              href={`#story-${story.id}`}
              className="mt-2 inline-block text-xs font-medium text-info hover:underline"
              data-testid={`story-link-${story.id}`}
            >
              View details
            </a>
          ) : null}
        </div>
      </div>
    </li>
  );
}

export function HealthStoriesCard() {
  const query = useHealthStories();
  const stories = useMemo<readonly HealthStory[]>(() => {
    // Defensive: the `/api/health/stories` payload is loosely
    // typed (`additionalProperties: true`), so a re-fetch can
    // return a non-array. Coerce before .sort/.map.
    const raw = query.data?.stories;
    const list: readonly HealthStory[] = Array.isArray(raw)
      ? (raw as readonly HealthStory[])
      : [];
    return [...list].sort(
      (a, b) =>
        (SEVERITY_RANK[(a.severity ?? "").toLowerCase()] ?? 9) -
        (SEVERITY_RANK[(b.severity ?? "").toLowerCase()] ?? 9),
    );
  }, [query.data]);

  return (
    <Card data-testid="health-stories-card">
      <CardHeader>
        <CardTitle>Health stories</CardTitle>
        <CardDescription>
          Plain-language narratives composed from the live probe set
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <div
            className="flex flex-col gap-2"
            data-testid="health-stories-loading"
          >
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="health-stories-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : isQuiet(stories) ? (
          <EmptyState
            icon={Sparkles}
            title="All systems quiet"
            description="No active stories. The controller will surface issues here as they appear."
          />
        ) : (
          <ul
            className="flex flex-col gap-2"
            data-testid="health-stories-list"
          >
            {stories.map((s) => (
              <StoryRow key={s.id} story={s} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
