import { useState } from "react";
import { Drawer as VaulDrawer } from "vaul";
import { Link } from "@tanstack/react-router";
import { ScrollText, X } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { cn } from "@/lib/cn";
import { useRun } from "./hooks";
import { ChildrenPanel, OutputPanel, SummaryPanel } from "./RunDrawerPanels";

const POLL_RUNNING_MS = 2_000;
const POLL_SETTLED_MS = 30_000;

interface RunDrawerProps {
  /** Run id to load. ``null`` keeps the drawer closed. */
  runId: string | null;
  onClose: () => void;
  /** Optional: forwarded so the consumer can react to a child-run
   *  click (e.g. by setting its own ``selectedRunId`` to drill in). */
  onSelectRunId?: (runId: string) => void;
}

/**
 * Per-run detail drawer. Slides in from the right and surfaces the
 * full ``RunRecord`` payload across three tabs (Summary / Output /
 * Children). The drawer manages no run-id state of its own — the
 * parent controls the open run via ``runId``. Closes when ``runId``
 * is ``null``.
 */
export function RunDrawer({
  runId,
  onClose,
  onSelectRunId,
}: RunDrawerProps): JSX.Element {
  const open = runId !== null;
  return (
    <VaulDrawer.Root
      direction="right"
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <VaulDrawer.Portal>
        <VaulDrawer.Overlay className="fixed inset-0 z-50 bg-[color-mix(in_oklab,var(--color-bg)_70%,transparent)] backdrop-blur-sm" />
        <VaulDrawer.Content
          className={cn(
            "fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-border bg-bg-1 outline-none",
          )}
          data-testid="run-drawer"
          data-run-id={runId ?? ""}
        >
          {runId ? (
            <DrawerBody
              runId={runId}
              onSelectRunId={onSelectRunId}
              onClose={onClose}
            />
          ) : null}
        </VaulDrawer.Content>
      </VaulDrawer.Portal>
    </VaulDrawer.Root>
  );
}

function DrawerBody({
  runId,
  onSelectRunId,
  onClose,
}: {
  runId: string;
  onSelectRunId?: (runId: string) => void;
  onClose: () => void;
}): JSX.Element {
  const [tab, setTab] = useState<"summary" | "output" | "children">("summary");
  // Adaptive refetch: hot polling while the operator's looking at
  // Summary/Output (which are the live-updating tabs); slow once they
  // drill into Children. Matches LastRunPanel cadence so two open
  // surfaces don't double-tap the controller.
  const isLiveTab = tab === "summary" || tab === "output";
  const detail = useRun(runId, {
    refetchInterval: isLiveTab ? POLL_RUNNING_MS : POLL_SETTLED_MS,
  });
  const childCount = detail.data?.children.length ?? 0;

  return (
    <>
      <header className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div className="flex min-w-0 flex-col gap-1">
          <VaulDrawer.Title className="truncate text-lg font-semibold leading-none tracking-tight">
            {detail.data?.job_name ?? "Run"}
          </VaulDrawer.Title>
          <VaulDrawer.Description className="truncate font-mono text-xs text-fg-muted">
            {runId}
          </VaulDrawer.Description>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-sm p-1 text-fg-muted [@media(hover:hover)]:hover:text-fg"
          aria-label="Close drawer"
          data-testid="run-drawer-close"
        >
          <X className="size-4" aria-hidden />
        </button>
      </header>

      <div className="flex-1 overflow-y-auto p-4">
        {detail.isLoading ? (
          <div className="space-y-2" data-testid="run-drawer-loading">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : detail.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="run-drawer-error"
          >
            Couldn&apos;t load this run: {(detail.error as Error).message}
          </p>
        ) : !detail.data ? (
          <p className="text-sm text-fg-muted" data-testid="run-drawer-empty">
            No record for this run id.
          </p>
        ) : (
          <Tabs
            value={tab}
            onValueChange={(v) => setTab(v as typeof tab)}
            className="flex flex-col gap-4"
          >
            <TabsList className="self-start">
              <TabsTrigger value="summary" data-testid="run-drawer-tab-summary">
                Summary
              </TabsTrigger>
              <TabsTrigger value="output" data-testid="run-drawer-tab-output">
                Output
              </TabsTrigger>
              <TabsTrigger
                value="children"
                data-testid="run-drawer-tab-children"
              >
                Children
                {childCount > 0 ? (
                  <span className="ml-1 text-fg-faint">{childCount}</span>
                ) : null}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="summary">
              <SummaryPanel run={detail.data} />
            </TabsContent>
            <TabsContent value="output">
              <OutputPanel run={detail.data} />
            </TabsContent>
            <TabsContent value="children">
              <ChildrenPanel
                children={detail.data.children}
                onSelectRunId={onSelectRunId}
              />
            </TabsContent>
          </Tabs>
        )}
      </div>

      <footer className="border-t border-border p-4">
        <Link
          to="/audit-log"
          search={{ action: detail.data?.job_name ?? "" }}
          className="inline-flex items-center gap-1.5 text-sm text-info underline-offset-2 [@media(hover:hover)]:hover:underline"
          data-testid="run-drawer-audit-link"
        >
          <ScrollText aria-hidden className="size-4" />
          View audit history for this job
        </Link>
      </footer>
    </>
  );
}
