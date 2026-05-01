import { useEffect, useMemo, useRef, useState, type JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetcher } from "@/api/client";
import { useJobs, useJobsRunning, useRunAction } from "@/features/jobs/hooks";
import {
  buildSetupExperienceState,
  type BootstrapStatus,
} from "./setupState";
import { SetupStatus } from "./setupStatusConstants";
import { BootstrapProgressBannerView } from "./BootstrapProgressBannerView";

const DISMISS_KEY = "media-stack:bootstrap-banner-dismissed";
const CELEBRATED_KEY = "media-stack:bootstrap-celebrated";
const STATUS_POLL_INTERVAL_MS = 2_000;
const CELEBRATION_HOLD_MS = 8_000;

/**
 * Production-side wrapper: subscribes to the live ``/status``,
 * ``/api/jobs/running``, and ``/api/jobs?history`` queries, derives
 * the ``SetupExperienceState`` via ``buildSetupExperienceState``,
 * and hands the data off to ``BootstrapProgressBannerView``.
 *
 * Pull this into the ``AppShell`` (chrome-level) so it appears
 * exactly once per session. For demo / Storybook surfaces that
 * want to render specific phases without going through the live
 * controller, use ``BootstrapProgressBannerView`` directly.
 */
export function BootstrapProgressBanner(): JSX.Element | null {
  const statusQuery = useQuery<BootstrapStatus>({
    queryKey: ["controller", "status"],
    queryFn: () => fetcher<BootstrapStatus>("status"),
    refetchInterval: STATUS_POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    staleTime: 1_000,
    retry: 1,
  });
  const runningQuery = useJobsRunning();
  const jobsQuery = useJobs();
  const retryBootstrap = useRunAction("bootstrap");

  const [dismissed, setDismissed] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(DISMISS_KEY) === "1";
  });
  useEffect(() => {
    if (!dismissed || typeof window === "undefined") return;
    window.localStorage.setItem(DISMISS_KEY, "1");
  }, [dismissed]);

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

  // Live elapsed re-tick while running — banner feels alive even
  // between status polls.
  useEffect(() => {
    if (setup.phase !== SetupStatus.Running) return undefined;
    const id = window.setInterval(() => setTick((t) => t + 1), 1_000);
    return () => window.clearInterval(id);
  }, [setup.phase]);

  // Celebration holds the success state for a beat then fades out
  // and yields the dashboard to the OnboardingChecklist.
  const [celebratedHidden, setCelebratedHidden] = useState(false);
  const celebrateTimer = useRef<number | null>(null);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (setup.phase !== SetupStatus.Complete) return;
    if (window.localStorage.getItem(CELEBRATED_KEY) === "1") {
      setCelebratedHidden(true);
      return;
    }
    if (celebrateTimer.current !== null) return;
    celebrateTimer.current = window.setTimeout(() => {
      window.localStorage.setItem(CELEBRATED_KEY, "1");
      setCelebratedHidden(true);
    }, CELEBRATION_HOLD_MS);
    return () => {
      if (celebrateTimer.current !== null) {
        window.clearTimeout(celebrateTimer.current);
        celebrateTimer.current = null;
      }
    };
  }, [setup.phase]);

  return (
    <BootstrapProgressBannerView
      setup={setup}
      dismissed={dismissed}
      celebratedHidden={celebratedHidden}
      onDismiss={() => setDismissed(true)}
      onRetry={() => void retryBootstrap.mutateAsync()}
      retryDisabled={retryBootstrap.isPending}
    />
  );
}
