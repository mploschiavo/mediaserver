import { useCallback, useEffect, useMemo, useState, type JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetcher } from "@/api/client";
import { useJobs, useJobsRunning, useRunAction } from "@/features/jobs/hooks";
import type { JobHistoryEntry, RunningTreeNodeShape } from "@/features/jobs/hooks";
import {
  buildSetupExperienceState,
  type ActionHistoryEntry,
  type BootstrapStatus,
} from "./setupState";
import { SetupStatus } from "./setupStatusConstants";
import { BootstrapProgressBannerView } from "./BootstrapProgressBannerView";

const DISMISSED_RUNS_KEY = "media-stack:bootstrap-dismissed-run";
const STATUS_POLL_INTERVAL_MS = 30_000;

/**
 * Identifies the bootstrap run currently surfaced by the banner.
 * Used as the per-run dismissal key so a re-bootstrap (new run)
 * automatically re-shows the banner without manual reset.
 *
 * Three signal sources, tried in order:
 *
 *   1. ``/api/jobs/running.tree`` — bootstrap root's ``run_id``
 *      (ULID/UUID). Only fires for Job-framework-routed bootstrap
 *      runs.
 *   2. ``/api/jobs?history`` — most recent bootstrap history
 *      entry's ``ts`` (Unix epoch seconds).
 *   3. ``/status::action_history`` — most recent bootstrap action's
 *      ``id`` (e.g. ``"bootstrap-1"``). The legacy
 *      ``action_trigger`` path that ``controller_serve`` uses on
 *      auto-run / re-bootstrap does NOT register with the Job
 *      framework — its records live ONLY in
 *      ``state.action_history``. Without this fallback, the per-
 *      run dismissal key is permanently ``null`` on every install
 *      that bootstraps via the legacy path, so the Close button
 *      becomes a no-op.
 *
 * Returns ``null`` only when none of the three has an entry —
 * pre-first-run / brand-new install with nothing started yet.
 */
function deriveCurrentRunKey(
  runningTree: readonly RunningTreeNodeShape[],
  history: readonly JobHistoryEntry[],
  actionHistory: readonly ActionHistoryEntry[],
): string | null {
  for (const node of runningTree) {
    if (node.job_name === "bootstrap" && node.run_id) {
      return `run:${node.run_id}`;
    }
  }
  for (const entry of history) {
    if (entry.jobs?.bootstrap && typeof entry.ts === "number") {
      return `ts:${entry.ts}`;
    }
  }
  for (const action of actionHistory) {
    if (action.name === "bootstrap" && action.id) {
      return `action:${action.id}`;
    }
  }
  return null;
}

/**
 * Production-side wrapper: subscribes to the live
 * ``/api/jobs/running`` + ``/api/jobs?history`` queries (the
 * canonical Job-framework view) plus a coarse-cadence ``/status``
 * for the deployment-state ``initial_bootstrap_done`` flag, derives
 * the ``SetupExperienceState`` via ``buildSetupExperienceState``,
 * and hands the data off to ``BootstrapProgressBannerView``.
 *
 * ADR-0005 Phase 5a: the banner consumes the bootstrap job through
 * the same Job-framework contract as every other job. The pre-Job
 * legacy ``/status`` shape (``current_action`` / ``phases_completed``
 * / legacy ``phase``) is no longer read.
 *
 * Pull this into the ``AppShell`` (chrome-level) so it appears
 * exactly once per session. For demo / Storybook surfaces that
 * want to render specific phases without going through the live
 * controller, use ``BootstrapProgressBannerView`` directly.
 */
export function BootstrapProgressBanner(): JSX.Element | null {
  const statusQuery = useQuery<BootstrapStatus>({
    queryKey: ["controller", "status"],
    queryFn: () => fetcher<BootstrapStatus>("api/status"),
    refetchInterval: STATUS_POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    staleTime: 10_000,
    retry: 1,
  });
  const runningQuery = useJobsRunning();
  const jobsQuery = useJobs();
  const retryBootstrap = useRunAction("bootstrap");

  const [, setTick] = useState(0);

  const setup = useMemo(
    () =>
      buildSetupExperienceState({
        status: statusQuery.data,
        statusReachable: !statusQuery.isError,
        runningTree: runningQuery.data?.tree ?? [],
        history: jobsQuery.data?.history ?? [],
      }),
    [
      statusQuery.data,
      statusQuery.isError,
      runningQuery.data?.tree,
      jobsQuery.data?.history,
    ],
  );

  const currentRunKey = useMemo(
    () =>
      deriveCurrentRunKey(
        runningQuery.data?.tree ?? [],
        jobsQuery.data?.history ?? [],
        statusQuery.data?.action_history ?? [],
      ),
    [
      runningQuery.data?.tree,
      jobsQuery.data?.history,
      statusQuery.data?.action_history,
    ],
  );

  // Per-run dismissal: stores the key of the most recent run the
  // operator clicked Close on. A new bootstrap (different run_id /
  // ts) automatically re-shows; no manual localStorage reset needed.
  const [lastDismissedKey, setLastDismissedKey] = useState<string | null>(
    () => {
      if (typeof window === "undefined") return null;
      return window.localStorage.getItem(DISMISSED_RUNS_KEY);
    },
  );
  const dismissed =
    currentRunKey !== null && currentRunKey === lastDismissedKey;
  const handleDismiss = useCallback(() => {
    if (currentRunKey === null) return;
    setLastDismissedKey(currentRunKey);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(DISMISSED_RUNS_KEY, currentRunKey);
    }
  }, [currentRunKey]);

  // Live elapsed re-tick while running — banner feels alive even
  // between query polls.
  useEffect(() => {
    if (setup.phase !== SetupStatus.Running) return undefined;
    const id = window.setInterval(() => setTick((t) => t + 1), 1_000);
    return () => window.clearInterval(id);
  }, [setup.phase]);

  return (
    <BootstrapProgressBannerView
      setup={setup}
      dismissed={dismissed}
      onDismiss={handleDismiss}
      onRetry={() => void retryBootstrap.mutateAsync()}
      retryDisabled={retryBootstrap.isPending}
    />
  );
}
