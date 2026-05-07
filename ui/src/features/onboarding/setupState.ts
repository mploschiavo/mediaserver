import type { JobHistoryEntry, RunningTreeNodeShape } from "@/features/jobs/hooks";
import { SetupStatus } from "./setupStatusConstants";

export type SetupPhase =
  | typeof SetupStatus.WarmingUp
  | typeof SetupStatus.Queued
  | typeof SetupStatus.Running
  | typeof SetupStatus.Complete
  | typeof SetupStatus.CompleteWithWarnings
  | typeof SetupStatus.Failed
  | typeof SetupStatus.Cancelled
  | typeof SetupStatus.TimedOut;

export interface SetupStepSummary {
  total: number;
  completed: number;
  running: number;
  failed: number;
  skipped: number;
}

export type TimelineStepStatus =
  | "pending"
  | typeof SetupStatus.Running
  | typeof SetupStatus.Ok
  | typeof SetupStatus.Error
  | typeof SetupStatus.Skipped;

export interface TimelineStep {
  id: string;
  label: string;
  status: TimelineStepStatus;
  detail?: string;
  elapsedSeconds?: number;
}

export interface SetupExperienceState {
  phase: SetupPhase;
  isVisible: boolean;
  isReady: boolean;
  title: string;
  description: string;
  activePath: string[];
  activeStepLabel: string | null;
  activeRunId: string | null;
  elapsedSeconds: number;
  summary: SetupStepSummary;
  statusTone: "info" | "success" | "warning" | "danger";
  timeline: readonly TimelineStep[];
  ctas: readonly SetupCta[];
}

export interface SetupCta {
  key: "view_details" | "view_logs" | "retry" | "open_apps" | "verify_health";
  label: string;
  href?: string;
  actionName?: string;
}

/**
 * One entry in ``state.action_history`` — kept on ``/status`` as
 * the authoritative record of every bootstrap-class action run.
 * The bootstrap action runs through the controller's legacy
 * ``action_trigger`` path (NOT the Job framework's runner), so it
 * never appears in ``/api/jobs/running`` or ``/api/jobs?history``.
 * ``action_history`` is the only durable per-run identifier the
 * banner has for the bootstrap action — used as a per-run
 * dismissal key in the wrapper.
 */
export interface ActionHistoryEntry {
  id?: string;
  name?: string;
  status?: string;
  started_at?: number | null;
  completed_at?: number | null;
}

/**
 * Slice of the controller's ``/status`` response that the bootstrap
 * banner consumes. ADR-0005 Phase 5a retired the legacy
 * ``current_action`` / ``phases_completed`` / ``phase`` fields —
 * those duplicated the Job framework's running-tree + history view
 * and caused multi-source tearing in this derivation.
 *
 * ``initial_bootstrap_done`` is a deployment-state flag (has this
 * install ever bootstrapped successfully?), not runtime state, so
 * it stays on ``/status`` with no Job-framework equivalent.
 *
 * ``action_history`` is needed by the wrapper for per-run
 * dismissal keying — the legacy ``bootstrap`` action doesn't
 * register with the Job framework, so it has no ``run_id`` in
 * ``/api/jobs/running`` or history entry in ``/api/jobs?history``.
 */
export interface BootstrapStatus {
  initial_bootstrap_done?: boolean;
  action_history?: readonly ActionHistoryEntry[];
}

interface BuildInput {
  status?: BootstrapStatus | null;
  statusReachable?: boolean;
  runningTree?: readonly RunningTreeNodeShape[] | null;
  history?: readonly JobHistoryEntry[] | null;
  nowSeconds?: number;
}

/**
 * Map a raw bootstrap step / action / job-name id to a friendly,
 * present-tense label. Lifting these onto the controller contract
 * is a follow-up; UI-side keeps the change small for now.
 */
const HUMANIZED_LABELS: Readonly<Record<string, string>> = {
  bootstrap: "Bootstrapping the stack",
  preflight: "Running preflight checks",
  prepare_host: "Preparing host directories",
  generate_secrets: "Generating service secrets",
  pull_images: "Pulling container images",
  start_services: "Starting services",
  configure_media_server: "Configuring media server",
  configure_jellyfin: "Configuring Jellyfin",
  configure_sonarr: "Configuring Sonarr",
  configure_radarr: "Configuring Radarr",
  configure_prowlarr: "Configuring Prowlarr",
  configure_bazarr: "Configuring Bazarr",
  configure_jellyseerr: "Configuring Jellyseerr",
  discover_api_keys: "Discovering API keys",
  seed_indexers: "Loading indexer catalog",
  prowlarr_seed_indexers: "Loading indexer catalog",
  configure_download_clients: "Wiring download clients",
  configure_qbittorrent: "Configuring qBittorrent",
  configure_sabnzbd: "Configuring SABnzbd",
  configure_routing: "Configuring routing",
  smoke_test: "Running smoke tests",
  finalize: "Finalizing setup",
};

export function humanizeStepLabel(rawId: string): string {
  if (!rawId) return "Working…";
  const direct = HUMANIZED_LABELS[rawId];
  if (direct) return direct;
  const lower = rawId.toLowerCase();
  if (HUMANIZED_LABELS[lower]) return HUMANIZED_LABELS[lower];
  // Fallback: replace separators, title-case the first word.
  const cleaned = rawId.replace(/[_\-:.]+/g, " ").trim();
  if (!cleaned) return "Working…";
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

function flattenTree(
  nodes: readonly RunningTreeNodeShape[],
): RunningTreeNodeShape[] {
  const out: RunningTreeNodeShape[] = [];
  const walk = (n: RunningTreeNodeShape) => {
    out.push(n);
    for (const c of n.children) walk(c);
  };
  for (const n of nodes) walk(n);
  return out;
}

function findBootstrapRoot(
  tree: readonly RunningTreeNodeShape[],
): RunningTreeNodeShape | null {
  for (const root of tree) {
    if (root.job_name === "bootstrap") return root;
  }
  return null;
}

function findDeepestRunningPath(
  node: RunningTreeNodeShape,
): RunningTreeNodeShape[] {
  let best: RunningTreeNodeShape[] = [];
  const walk = (cur: RunningTreeNodeShape, trail: RunningTreeNodeShape[]) => {
    const next = [...trail, cur];
    if (cur.status === SetupStatus.Running) {
      if (next.length >= best.length) best = next;
    }
    for (const child of cur.children) walk(child, next);
  };
  walk(node, []);
  return best;
}

function summarizeRunningTree(node: RunningTreeNodeShape): SetupStepSummary {
  const flat = flattenTree([node]);
  let completed = 0;
  let running = 0;
  let failed = 0;
  let skipped = 0;
  for (const r of flat) {
    const st = String(r.status || "").toLowerCase();
    if (st === SetupStatus.Running) running += 1;
    else if (st === SetupStatus.Ok) completed += 1;
    else if (
      st === SetupStatus.Error ||
      st === SetupStatus.Timeout ||
      st === SetupStatus.Cancelled
    ) {
      failed += 1;
    } else if (st === SetupStatus.Skipped) skipped += 1;
  }
  return {
    total: flat.length,
    completed,
    running,
    failed,
    skipped,
  };
}

function timelineFromRunningTree(
  node: RunningTreeNodeShape,
): TimelineStep[] {
  const flat = flattenTree([node]);
  const out: TimelineStep[] = [];
  for (const n of flat) {
    const st = String(n.status || "").toLowerCase();
    let mapped: TimelineStepStatus = "pending";
    if (st === SetupStatus.Running) mapped = SetupStatus.Running;
    else if (st === SetupStatus.Ok) mapped = SetupStatus.Ok;
    else if (
      st === SetupStatus.Error ||
      st === SetupStatus.Timeout ||
      st === SetupStatus.Cancelled
    ) {
      mapped = SetupStatus.Error;
    } else if (st === SetupStatus.Skipped) mapped = SetupStatus.Skipped;
    out.push({
      id: n.run_id || n.job_name,
      label: humanizeStepLabel(n.job_name),
      status: mapped,
      elapsedSeconds: Number.isFinite(n.elapsed_seconds)
        ? Math.max(0, Math.floor(n.elapsed_seconds))
        : undefined,
    });
  }
  return out;
}

function historyForBootstrap(
  history: readonly JobHistoryEntry[],
): {
  status:
    | typeof SetupStatus.Ok
    | typeof SetupStatus.Error
    | typeof SetupStatus.Skipped
    | typeof SetupStatus.Cancelled
    | typeof SetupStatus.Timeout
    | "none";
  errorCount: number;
} {
  for (const entry of history) {
    const jobs = entry.jobs ?? {};
    const b = jobs.bootstrap;
    if (!b) continue;
    const st = String(b.status || "").toLowerCase();
    if (st === SetupStatus.Ok) {
      return { status: SetupStatus.Ok, errorCount: entry.errors ?? 0 };
    }
    if (st === SetupStatus.Error) {
      return { status: SetupStatus.Error, errorCount: entry.errors ?? 0 };
    }
    if (st === SetupStatus.Skipped) {
      return { status: SetupStatus.Skipped, errorCount: entry.errors ?? 0 };
    }
    if (st === SetupStatus.Cancelled) {
      return { status: SetupStatus.Cancelled, errorCount: entry.errors ?? 0 };
    }
    if (st === SetupStatus.Timeout) {
      return { status: SetupStatus.Timeout, errorCount: entry.errors ?? 0 };
    }
  }
  return { status: "none", errorCount: 0 };
}

const EMPTY_SUMMARY: SetupStepSummary = {
  total: 0,
  completed: 0,
  running: 0,
  failed: 0,
  skipped: 0,
};

export function buildSetupExperienceState({
  status,
  runningTree,
  history,
  nowSeconds,
}: BuildInput): SetupExperienceState {
  const state = status ?? {};
  const tree = Array.isArray(runningTree) ? runningTree : [];
  const hist = Array.isArray(history) ? history : [];
  const now = nowSeconds ?? Date.now() / 1000;

  // Controller is unreachable OR ``/api/status`` hasn't responded
  // yet. Render a soft skeleton state so the dashboard never feels
  // empty during the first few seconds of the operator's
  // experience — and, critically, doesn't briefly flash the
  // "Queued — waiting for the controller…" copy before the live
  // ``/api/status`` query resolves with the real
  // ``initial_bootstrap_done`` flag. ``status === undefined`` means
  // the wrapper's ``useQuery`` has nothing yet (loading or
  // errored); both shapes deserve the WarmingUp affordance until
  // we have a verdict.
  if (!status) {
    return {
      phase: SetupStatus.WarmingUp,
      isVisible: true,
      isReady: false,
      title: "Reaching the controller…",
      description:
        "Your stack is starting up. We'll show progress here as soon as the controller responds.",
      activePath: [],
      activeStepLabel: null,
      activeRunId: null,
      elapsedSeconds: 0,
      summary: EMPTY_SUMMARY,
      statusTone: "info",
      timeline: [],
      ctas: [],
    };
  }

  const bootstrapRoot = findBootstrapRoot(tree);
  const histBootstrap = historyForBootstrap(hist);
  // ``state.initial_bootstrap_done`` is an in-memory flag on the
  // controller — it resets to ``false`` on every controller
  // restart (e.g. an image bake/redeploy). On a fresh restart of
  // an already-bootstrapped install, that would otherwise wedge
  // the banner in the "Queued — waiting for the controller…"
  // state forever. Deriving "first-run done" from job history (a
  // single prior bootstrap entry of ANY status — ok / error /
  // cancelled / timeout) makes the signal durable across
  // restarts: once you've ever bootstrapped, you're past the
  // first-run window.
  const initialDone =
    Boolean(state.initial_bootstrap_done) || histBootstrap.status !== "none";

  // Tree path wins when present — most precise live signal.
  if (bootstrapRoot) {
    const pathNodes = findDeepestRunningPath(bootstrapRoot);
    const path = pathNodes.map((n) => n.job_name);
    const active = pathNodes[pathNodes.length - 1] ?? bootstrapRoot;
    const elapsed = Math.max(
      0,
      Number.isFinite(active.elapsed_seconds)
        ? active.elapsed_seconds
        : now - active.started_at,
    );
    const activeLabel =
      active && active.job_name ? humanizeStepLabel(active.job_name) : null;
    return {
      phase: SetupStatus.Running,
      isVisible: true,
      isReady: false,
      title: "Setting up your media stack",
      description:
        path.length > 0
          ? `Running ${path.join(" > ")}`
          : "Bootstrap job is running.",
      activePath: path,
      activeStepLabel: activeLabel,
      activeRunId: active.run_id,
      elapsedSeconds: elapsed,
      summary: summarizeRunningTree(bootstrapRoot),
      statusTone: "info",
      timeline: timelineFromRunningTree(bootstrapRoot),
      ctas: [{ key: "view_details", label: "View setup details", href: "/jobs?filter=bootstrap" }],
    };
  }

  if (!initialDone) {
    return {
      phase: SetupStatus.Queued,
      isVisible: true,
      isReady: false,
      title: "Setting up your media stack",
      description: "Waiting for the controller to pick up the bootstrap job…",
      activePath: [],
      activeStepLabel: null,
      activeRunId: null,
      elapsedSeconds: 0,
      summary: EMPTY_SUMMARY,
      statusTone: "info",
      timeline: [],
      ctas: [{ key: "view_details", label: "View setup details", href: "/jobs?filter=bootstrap" }],
    };
  }

  // Terminal states once initial bootstrap is done.
  if (histBootstrap.status === "error") {
    return {
      phase: SetupStatus.Failed,
      isVisible: true,
      isReady: false,
      title: "Setup needs attention",
      description:
        "Bootstrap completed with blocking errors. Review details, then retry setup.",
      activePath: [],
      activeStepLabel: null,
      activeRunId: null,
      elapsedSeconds: 0,
      summary: { ...EMPTY_SUMMARY, failed: Math.max(1, histBootstrap.errorCount) },
      statusTone: "danger",
      timeline: [],
      ctas: [
        { key: "view_details", label: "View failing step", href: "/jobs?filter=bootstrap" },
        { key: "retry", label: "Retry setup", actionName: "bootstrap" },
      ],
    };
  }

  if (histBootstrap.status === SetupStatus.Cancelled) {
    return {
      phase: SetupStatus.Cancelled,
      isVisible: true,
      isReady: false,
      title: "Setup was cancelled",
      description: "Setup was interrupted before completion. Retry to finish configuration.",
      activePath: [],
      activeStepLabel: null,
      activeRunId: null,
      elapsedSeconds: 0,
      summary: { ...EMPTY_SUMMARY, failed: 1 },
      statusTone: "warning",
      timeline: [],
      ctas: [
        { key: "view_details", label: "View setup details", href: "/jobs?filter=bootstrap" },
        { key: "retry", label: "Retry setup", actionName: "bootstrap" },
      ],
    };
  }

  if (histBootstrap.status === "timeout") {
    return {
      phase: SetupStatus.TimedOut,
      isVisible: true,
      isReady: false,
      title: "Setup timed out",
      description: "Setup exceeded its runtime cap. Review logs and retry setup.",
      activePath: [],
      activeStepLabel: null,
      activeRunId: null,
      elapsedSeconds: 0,
      summary: { ...EMPTY_SUMMARY, failed: 1 },
      statusTone: "warning",
      timeline: [],
      ctas: [
        { key: "view_details", label: "View logs", href: "/jobs?filter=bootstrap" },
        { key: "retry", label: "Retry setup", actionName: "bootstrap" },
      ],
    };
  }

  if (histBootstrap.errorCount > 0) {
    return {
      phase: SetupStatus.CompleteWithWarnings,
      isVisible: true,
      isReady: false,
      title: "Setup completed with warnings",
      description:
        "Core setup finished, but some follow-up steps need attention.",
      activePath: [],
      activeStepLabel: null,
      activeRunId: null,
      elapsedSeconds: 0,
      summary: { ...EMPTY_SUMMARY, failed: Math.max(1, histBootstrap.errorCount) },
      statusTone: "warning",
      timeline: [],
      ctas: [
        { key: "view_details", label: "Review warnings", href: "/jobs?filter=bootstrap" },
        { key: "verify_health", label: "Verify system health", href: "/ops" },
      ],
    };
  }

  return {
    phase: SetupStatus.Complete,
    isVisible: true,
    isReady: true,
    title: "Your media stack is ready",
    description:
      "Initial setup is complete. You can start using the stack now.",
    activePath: [],
    activeStepLabel: null,
    activeRunId: null,
    elapsedSeconds: 0,
    summary: EMPTY_SUMMARY,
    statusTone: "success",
    timeline: [],
    ctas: [
      { key: "open_apps", label: "Open apps", href: "/apps" },
      { key: "verify_health", label: "Verify system health", href: "/ops" },
      { key: "view_details", label: "View setup details", href: "/jobs?filter=bootstrap" },
    ],
  };
}
