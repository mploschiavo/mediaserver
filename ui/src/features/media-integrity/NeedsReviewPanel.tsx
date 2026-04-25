import { useMemo } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import {
  ApiError,
  useResolveReview,
  type MediaIntegrityStatusShape,
  type ResolveReviewInput,
} from "@/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatBytes } from "./format";

interface NeedsReviewPanelProps {
  status?: MediaIntegrityStatusShape;
}

interface ReviewCandidate {
  file_id: string;
  size: number;
}

interface ReviewItem {
  app: string;
  release_id: string;
  release_kind?: string;
  title: string;
  candidates: ReviewCandidate[];
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

/** Read the "needs review" items out of the opaque report detail. */
function buildReviewItems(
  detail: Record<string, unknown> | undefined,
): ReviewItem[] {
  if (!detail) return [];
  const out: ReviewItem[] = [];

  const servarr = detail.servarr;
  if (
    servarr &&
    typeof servarr === "object" &&
    "results" in servarr &&
    typeof (servarr as Record<string, unknown>).results === "object"
  ) {
    const results = (servarr as { results: Record<string, unknown> }).results;
    for (const [app, r] of Object.entries(results)) {
      if (!r || typeof r !== "object") continue;
      const rec = r as Record<string, unknown>;
      const arr = rec.needs_review;
      if (!Array.isArray(arr)) continue;
      for (const it of arr) {
        if (!it || typeof it !== "object") continue;
        const item = it as Record<string, unknown>;
        const candidates = Array.isArray(item.candidates)
          ? (item.candidates as Array<Record<string, unknown>>)
              .map((c) => ({
                file_id: String(c.file_id ?? c.id ?? ""),
                size: num(c.size),
              }))
              .filter((c) => c.file_id !== "")
          : [];
        out.push({
          app,
          release_id: String(item.release_id ?? ""),
          release_kind:
            typeof item.release_kind === "string"
              ? item.release_kind
              : undefined,
          title: String(item.title ?? item.release_id ?? "Unknown release"),
          candidates,
        });
      }
    }
  }

  return out;
}

export function NeedsReviewPanel({ status }: NeedsReviewPanelProps) {
  const reduce = useReducedMotion();
  const resolve = useResolveReview();
  const items = useMemo(
    () =>
      buildReviewItems(status?.last_reconcile?.detail as Record<string, unknown> | undefined),
    [status],
  );

  if (!status || items.length === 0) return null;

  const handleKeep = (item: ReviewItem, winner: ReviewCandidate) => {
    const body: ResolveReviewInput = {
      app: item.app,
      release_id: item.release_id,
      winner_file_id: winner.file_id,
    };
    if (item.release_kind) body.release_kind = item.release_kind;
    resolve.mutate(
      { body },
      {
        onSuccess: (out) => {
          const deleted = out?.deleted_ids?.length ?? 0;
          toast.success(
            `Kept ${winner.file_id} — deleted ${deleted} other${deleted === 1 ? "" : "s"}`,
          );
        },
        onError: (err) => {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Resolve failed";
          toast.error(msg);
        },
      },
    );
  };

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      data-testid="needs-review-panel"
    >
      <Card>
        <CardHeader className="border-b border-[color-mix(in_oklab,var(--color-warning)_25%,transparent)] bg-[color-mix(in_oklab,var(--color-warning)_8%,transparent)] [border-bottom-left-radius:0] [border-bottom-right-radius:0]">
          <CardTitle className="flex items-center gap-2">
            <AlertTriangle className="size-4 text-warning" aria-hidden />
            Needs review
          </CardTitle>
          <CardDescription>
            {items.length} release{items.length === 1 ? "" : "s"} the engine
            couldn't auto-resolve. Pick the keeper.
          </CardDescription>
        </CardHeader>
        <CardContent className="divide-y divide-border p-0">
          {items.map((item) => (
            <div
              key={`${item.app}:${item.release_id}`}
              className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center sm:justify-between"
              data-testid="review-item"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-fg">
                  {item.title}
                </div>
                <div className="text-xs text-fg-muted">
                  {item.app} · {item.candidates.length} candidate
                  {item.candidates.length === 1 ? "" : "s"}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {item.candidates.map((c) => (
                  <Button
                    key={c.file_id}
                    size="sm"
                    variant="secondary"
                    onClick={() => handleKeep(item, c)}
                    disabled={resolve.isPending}
                    data-testid={`keep-${item.release_id}-${c.file_id}`}
                  >
                    Keep {formatBytes(c.size)}
                  </Button>
                ))}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </motion.div>
  );
}
