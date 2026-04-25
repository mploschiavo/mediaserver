// Tiny client-side alert engine. Polls `/api/health` every 30s
// (via the operator-supplied `pollHealth` callback so the engine
// never imports React-Query directly), evaluates each rule from
// `useAlertRules`'s storage, and fires a `sonner` toast when a
// rule's consecutive-failure threshold is hit.
//
// This restores the dashboard.html legacy logic at line 3577
// (`function checkAlerts()`) while staying decoupled from any
// React tree: the engine is a plain start/stop facade.

import { toast } from "sonner";

import { asArray } from "@/lib/coerce";

import {
  getAlertRules,
  subscribeAlertRules,
  type AlertRule,
} from "./hooks";

/** A health probe — keep loose to match the OpenAPI's open-shape. */
export interface HealthLike {
  status?: string;
  [key: string]: unknown;
}

/** Default 30s poll interval. Exposed for tests + tuning. */
export const ALERT_ENGINE_INTERVAL_MS = 30_000;

interface StartOptions {
  /** Returns the current health snapshot (or undefined if not loaded). */
  pollHealth: () => HealthLike | undefined;
  /** Optional override of the poll cadence. Defaults to 30s. */
  intervalMs?: number;
  /** Toast emitter; injected for tests. */
  emit?: (message: string) => void;
}

/** Handle returned by `startAlertEngine` so callers can tear down. */
export interface AlertEngineHandle {
  stop: () => void;
}

/**
 * Walk a HealthShape and surface a `{ id -> status }` map. The
 * controller has shipped this payload in two shapes over the
 * versions: a top-level `services` array and a flat record. We
 * accept either and fall back to the single top-level `status`.
 */
export function readServiceStatuses(
  health: HealthLike | undefined,
): Record<string, string> {
  if (!health || typeof health !== "object") return {};

  const out: Record<string, string> = {};

  // Shape A: { services: [{ id, status }, ...] }
  const services = asArray<{ id?: unknown; name?: unknown; status?: unknown }>(
    (health as { services?: unknown }).services,
  );
  for (const svc of services) {
    const id = typeof svc.id === "string" ? svc.id : undefined;
    const name =
      typeof svc.name === "string" && svc.name.length > 0
        ? svc.name
        : undefined;
    const key = id ?? name;
    if (!key) continue;
    out[key] =
      typeof svc.status === "string" ? svc.status.toLowerCase() : "unknown";
  }

  // Shape B: { sonarr: { status: "ok" }, radarr: { status: "down" } }
  for (const [key, value] of Object.entries(health)) {
    if (key === "services" || key === "status") continue;
    if (value && typeof value === "object" && "status" in value) {
      const status = (value as { status?: unknown }).status;
      if (typeof status === "string") {
        out[key] = status.toLowerCase();
      }
    }
  }

  // Shape C fallback: a single global status.
  if (typeof health.status === "string" && Object.keys(out).length === 0) {
    out["controller"] = health.status.toLowerCase();
  }

  return out;
}

/**
 * Decide whether a service's status trips a rule's condition. The
 * condition vocabulary maps to the kinds the controller's probes
 * emit ("ok" | "degraded" | "down" | "unknown" | "error" | ...).
 */
export function statusMatchesCondition(
  status: string | undefined,
  condition: AlertRule["condition"],
): boolean {
  if (!status) return false;
  const normalised = status.toLowerCase();
  switch (condition) {
    case "down":
      return (
        normalised === "down" ||
        normalised === "error" ||
        normalised === "fail" ||
        normalised === "failed"
      );
    case "degraded":
      return normalised === "degraded" || normalised === "warn";
    case "any":
      return normalised !== "ok" && normalised !== "healthy";
    default:
      return false;
  }
}

interface EvaluationState {
  /** rule.id -> service-key -> consecutive matches */
  consecutive: Map<string, Map<string, number>>;
  /** rule.id -> service-key already-fired flag (re-arm on recovery). */
  fired: Map<string, Set<string>>;
}

function makeState(): EvaluationState {
  return {
    consecutive: new Map(),
    fired: new Map(),
  };
}

/**
 * Run one evaluation pass. Exposed for tests so a deterministic
 * sequence of (health, rules) can be fed in without timers.
 */
export function evaluateRulesOnce(
  rules: readonly AlertRule[],
  health: HealthLike | undefined,
  state: EvaluationState,
  emit: (message: string) => void,
): void {
  const statuses = readServiceStatuses(health);
  const knownKeys = Object.keys(statuses);

  for (const rule of rules) {
    const targetKeys =
      rule.service === "*" || rule.service === ""
        ? knownKeys
        : [rule.service];

    let perRule = state.consecutive.get(rule.id);
    if (!perRule) {
      perRule = new Map();
      state.consecutive.set(rule.id, perRule);
    }
    let firedSet = state.fired.get(rule.id);
    if (!firedSet) {
      firedSet = new Set();
      state.fired.set(rule.id, firedSet);
    }

    for (const key of targetKeys) {
      const status = statuses[key];
      const matched = statusMatchesCondition(status, rule.condition);
      if (matched) {
        const next = (perRule.get(key) ?? 0) + 1;
        perRule.set(key, next);
        if (next >= rule.threshold && !firedSet.has(key)) {
          firedSet.add(key);
          emit(
            `[${rule.name}] ${key} has been ${rule.condition} for ${rule.threshold} checks`,
          );
        }
      } else {
        perRule.set(key, 0);
        firedSet.delete(key);
      }
    }
  }
}

/**
 * Start the alert engine. Subscribes to rule-list changes (so
 * additions / deletions take effect immediately) and ticks every
 * `intervalMs` ms. Returns a handle whose `stop()` clears the
 * interval and the rule-subscription. Idempotent — calling
 * `stop()` twice is safe.
 */
export function startAlertEngine(opts: StartOptions): AlertEngineHandle {
  const interval = opts.intervalMs ?? ALERT_ENGINE_INTERVAL_MS;
  const emit = opts.emit ?? ((msg: string) => toast.warning(msg));
  const state = makeState();

  let rules: readonly AlertRule[] = getAlertRules();
  const unsubscribe = subscribeAlertRules((next) => {
    rules = next;
    // Discard fired flags for rules that no longer exist so that
    // re-creating a rule with a fresh id starts cold.
    const ids = new Set(rules.map((r) => r.id));
    for (const id of [...state.consecutive.keys()]) {
      if (!ids.has(id)) state.consecutive.delete(id);
    }
    for (const id of [...state.fired.keys()]) {
      if (!ids.has(id)) state.fired.delete(id);
    }
  });

  const tick = () => {
    if (rules.length === 0) return;
    const health = opts.pollHealth();
    evaluateRulesOnce(rules, health, state, emit);
  };

  // Run once immediately so an at-start failure is caught even
  // before the first interval boundary.
  tick();

  let timer: ReturnType<typeof setInterval> | null = null;
  if (typeof window !== "undefined" || typeof setInterval !== "undefined") {
    timer = setInterval(tick, interval);
  }

  let stopped = false;
  return {
    stop: () => {
      if (stopped) return;
      stopped = true;
      if (timer !== null) clearInterval(timer);
      unsubscribe();
    },
  };
}
