// Feature-local hooks for the /settings -> Alerts surface. The
// OpenAPI spec at `src/media_stack/api/openapi.yaml` does not
// expose any alert-rule endpoint (only `/api/health`,
// `/api/health/stories`, `/api/health-history`, etc.), so the
// prior dashboard's pattern of `localStorage`-backed rules
// (`dashboard.html` line 3567 — `JSON.parse(localStorage.getItem(
// 'alertRules') || '[]')`) is the right shape to restore here.
//
// We persist under the namespaced key `media-stack:alert-rules`
// so a sibling agent's storage cannot collide with ours, and we
// expose a sync API:
//
//   const { rules, save, remove } = useAlertRules();
//
// Components subscribe via the `subscribe()` event bus so the
// AlertEngine sees the same rule list the card mutates without
// either having to call into the React tree.
//
// Backend reference: src/media_stack/api/openapi.yaml — confirmed
// no alert-rule operationId on any of the surveyed paths.
//
// asArray() from `@/lib/coerce` is used to defang any malformed
// JSON blob the user (or a stale browser snapshot) may have
// left behind in localStorage.

import { useCallback, useEffect, useState } from "react";

import { asArray } from "@/lib/coerce";

/**
 * One alert rule. Shape mirrors the dashboard.html legacy entry —
 * `{ svc, threshold }` — but with explicit names for the new card
 * surface (target service, condition, threshold, action).
 */
export interface AlertRule {
  /** UUID-ish id, generated client-side. Stable across renders. */
  id: string;
  /** Display label, e.g. "Sonarr down for 5 minutes". */
  name: string;
  /** Target service id ("*" matches any health entry). */
  service: string;
  /** Condition keyword. Currently only "down" is wired. */
  condition: "down" | "degraded" | "any";
  /** Consecutive-check threshold before the rule fires. */
  threshold: number;
  /** Action selector. Only "toast" is wired in the client engine. */
  action: "toast";
}

/** localStorage key. The `media-stack:` prefix avoids cross-app collisions. */
export const ALERT_RULES_STORAGE_KEY = "media-stack:alert-rules";

type Listener = (rules: readonly AlertRule[]) => void;

const listeners = new Set<Listener>();

function readFromStorage(): readonly AlertRule[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(ALERT_RULES_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    // Coerce defensively — a previous build may have written a
    // different shape (object map, malformed string, etc.).
    return asArray<AlertRule>(parsed).filter(isValidRule);
  } catch {
    // Corrupt JSON: treat as empty rather than crash the surface.
    return [];
  }
}

function isValidRule(value: unknown): value is AlertRule {
  if (!value || typeof value !== "object") return false;
  const r = value as Partial<AlertRule>;
  return (
    typeof r.id === "string" &&
    typeof r.name === "string" &&
    typeof r.service === "string" &&
    (r.condition === "down" ||
      r.condition === "degraded" ||
      r.condition === "any") &&
    typeof r.threshold === "number" &&
    Number.isFinite(r.threshold) &&
    r.action === "toast"
  );
}

function writeToStorage(rules: readonly AlertRule[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      ALERT_RULES_STORAGE_KEY,
      JSON.stringify(rules),
    );
  } catch {
    // Quota / private-mode failures: swallow. The in-memory copy
    // is still valid for the session; the next call will retry.
  }
}

function notify(rules: readonly AlertRule[]): void {
  for (const fn of listeners) {
    try {
      fn(rules);
    } catch {
      // Listeners must not break the bus.
    }
  }
}

/**
 * Subscribe to rule-list changes. Returns an unsubscribe fn. Used
 * by `AlertEngine` so the engine and card share one source of
 * truth without round-tripping through React state.
 */
export function subscribeAlertRules(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** One-shot read. The engine starts here, then subscribes for updates. */
export function getAlertRules(): readonly AlertRule[] {
  return readFromStorage();
}

/**
 * React hook that mirrors `localStorage` into component state and
 * exposes save/remove helpers. Every mutation writes through to
 * storage, then fans out to subscribers (including other tabs via
 * the `storage` event listener below).
 */
export function useAlertRules(): {
  rules: readonly AlertRule[];
  save: (rule: AlertRule) => void;
  remove: (id: string) => void;
  replaceAll: (next: readonly AlertRule[]) => void;
} {
  const [rules, setRules] = useState<readonly AlertRule[]>(() =>
    readFromStorage(),
  );

  // Keep this hook in sync with cross-component / cross-tab writes.
  useEffect(() => {
    const onLocal: Listener = (next) => setRules(next);
    listeners.add(onLocal);

    const onStorage = (event: StorageEvent) => {
      if (event.key !== ALERT_RULES_STORAGE_KEY) return;
      setRules(readFromStorage());
    };
    if (typeof window !== "undefined") {
      window.addEventListener("storage", onStorage);
    }

    return () => {
      listeners.delete(onLocal);
      if (typeof window !== "undefined") {
        window.removeEventListener("storage", onStorage);
      }
    };
  }, []);

  const replaceAll = useCallback((next: readonly AlertRule[]) => {
    writeToStorage(next);
    setRules(next);
    notify(next);
  }, []);

  const save = useCallback(
    (rule: AlertRule) => {
      const current = readFromStorage();
      const idx = current.findIndex((r) => r.id === rule.id);
      const next =
        idx === -1
          ? [...current, rule]
          : current.map((r) => (r.id === rule.id ? rule : r));
      replaceAll(next);
    },
    [replaceAll],
  );

  const remove = useCallback(
    (id: string) => {
      const current = readFromStorage();
      replaceAll(current.filter((r) => r.id !== id));
    },
    [replaceAll],
  );

  return { rules, save, remove, replaceAll };
}

/** Generate a stable id without pulling in `crypto.randomUUID` polyfills. */
export function newAlertRuleId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  // Cheap fallback for jsdom + ancient browsers.
  return `rule-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
