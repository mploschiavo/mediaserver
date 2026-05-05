import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import type { DiskGuardrailTransition } from "./hooks";

interface StorageTransitionFeedProps {
  transitions: readonly DiskGuardrailTransition[];
  /** Maximum entries to render. Older rows are trimmed. */
  limit?: number;
}

function formatTs(epochSeconds?: number): string {
  if (!epochSeconds || epochSeconds <= 0) return "—";
  return new Date(epochSeconds * 1000).toLocaleString();
}

/** Tone for the action chip — engages are warning, releases success,
 *  cleanups info, anything else neutral. */
function actionTone(action?: string): {
  variant: "success" | "warning" | "info" | "default";
  tone: "success" | "warning" | "info" | "muted";
} {
  if (!action) return { variant: "default", tone: "muted" };
  if (action.includes("released")) return { variant: "success", tone: "success" };
  if (action.includes("engaged")) return { variant: "warning", tone: "warning" };
  if (action.includes("cleanup")) return { variant: "info", tone: "info" };
  return { variant: "default", tone: "muted" };
}

export function StorageTransitionFeed({
  transitions,
  limit = 10,
}: StorageTransitionFeedProps) {
  // Per `feedback_empty_state_visibility` we always render the card —
  // never a `transitions.length > 0 && <Feed/>` gate. Empty list
  // shows a clear caption.
  const rows = [...transitions]
    .sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0))
    .slice(0, limit);

  return (
    <div
      className="flex flex-col gap-2"
      data-testid="storage-transition-feed"
    >
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-fg-faint">
          Recent transitions
        </span>
        <Link
          to="/audit-log"
          className="text-xs text-accent [@media(hover:hover)]:hover:underline"
          data-testid="storage-transition-feed-show-all"
        >
          Show all in audit log →
        </Link>
      </div>
      {rows.length === 0 ? (
        <div
          className="rounded-md border border-dashed border-border bg-bg-1/40 px-3 py-4 text-center text-sm text-fg-muted"
          data-testid="storage-transition-feed-empty"
        >
          No recent transitions
        </div>
      ) : (
        <ul
          className="divide-y divide-border rounded-md border border-border bg-bg-1/40"
          role="list"
          data-testid="storage-transition-feed-list"
        >
          {rows.map((row, i) => {
            const tone = actionTone(row.action);
            return (
              <li
                key={`${row.ts ?? i}-${row.action ?? i}`}
                className="flex flex-col gap-1 px-3 py-2 text-sm sm:flex-row sm:items-center sm:justify-between"
                data-testid={`storage-transition-row-${i}`}
              >
                <div className="flex items-center gap-2">
                  <Badge
                    variant={tone.variant}
                    data-tone={tone.tone}
                    data-testid={`storage-transition-action-${i}`}
                  >
                    {row.action ?? "—"}
                  </Badge>
                  <span className="text-xs text-fg-muted">
                    actor:{" "}
                    <span className="font-mono text-fg">
                      {row.actor ?? "—"}
                    </span>
                  </span>
                </div>
                <div className="flex items-center gap-3 text-xs text-fg-muted">
                  {typeof row.used_percent === "number" ? (
                    <span
                      className="font-mono tabular-nums"
                      data-testid={`storage-transition-percent-${i}`}
                    >
                      {row.used_percent.toFixed(1)}%
                    </span>
                  ) : null}
                  <span data-testid={`storage-transition-ts-${i}`}>
                    {formatTs(row.ts)}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
