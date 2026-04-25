import { motion, useReducedMotion } from "framer-motion";
import { ExternalLink } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfileYaml } from "./hooks";

const EXCERPT_LINES = 20;

function readYaml(p: { yaml?: string; content?: string } | undefined): string {
  if (!p) return "";
  if (typeof p.yaml === "string") return p.yaml;
  if (typeof p.content === "string") return p.content;
  return "";
}

function excerpt(text: string, lines: number): string {
  const split = text.split("\n");
  if (split.length <= lines) return text;
  return [...split.slice(0, lines), `… (${split.length - lines} more lines)`].join(
    "\n",
  );
}

/**
 * /profile — read-only YAML excerpt. The full editor lives at
 * `/settings`. Renders the first ~20 lines so operators can
 * peek at the bootstrap profile without context-switching.
 */
export function ProfileViewPage() {
  const reduce = useReducedMotion();
  const profile = useProfileYaml();
  const yaml = readYaml(profile.data);

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      data-testid="profile-view-page"
    >
      <PageHeader
        title="Profile"
        description="Bootstrap profile snapshot. Edit in Settings."
        actions={
          <Button asChild variant="secondary" data-testid="profile-open-settings">
            <a href="/settings">
              Open in Settings
              <ExternalLink aria-hidden className="size-3.5" />
            </a>
          </Button>
        }
      />
      <Card>
        <CardHeader>
          <CardTitle>Profile YAML</CardTitle>
          <CardDescription>
            Read-only excerpt — first {EXCERPT_LINES} lines.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {profile.isLoading ? (
            <Skeleton
              className="h-48 w-full"
              data-testid="profile-view-loading"
            />
          ) : profile.error ? (
            <div
              role="alert"
              data-testid="profile-view-error"
              className="text-sm text-danger"
            >
              {profile.error.message}
            </div>
          ) : yaml ? (
            <pre
              data-testid="profile-view-excerpt"
              className="max-h-[20rem] overflow-auto rounded-md border border-border bg-bg-1 p-3 font-mono text-xs text-fg"
            >
              {excerpt(yaml, EXCERPT_LINES)}
            </pre>
          ) : (
            <p
              className="text-sm text-fg-muted"
              data-testid="profile-view-empty"
            >
              No profile loaded.
            </p>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}
