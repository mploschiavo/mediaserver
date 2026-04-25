import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  evaluateRulesOnce,
  readServiceStatuses,
  startAlertEngine,
  statusMatchesCondition,
} from "./AlertEngine";
import { ALERT_RULES_STORAGE_KEY, type AlertRule } from "./hooks";

function makeRule(overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    id: overrides.id ?? "r1",
    name: overrides.name ?? "Sonarr down",
    service: overrides.service ?? "sonarr",
    condition: overrides.condition ?? "down",
    threshold: overrides.threshold ?? 2,
    action: overrides.action ?? "toast",
  };
}

beforeEach(() => {
  window.localStorage.clear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  window.localStorage.clear();
});

describe("readServiceStatuses", () => {
  it("returns empty for nullish health", () => {
    expect(readServiceStatuses(undefined)).toEqual({});
  });

  it("reads a top-level services array", () => {
    const out = readServiceStatuses({
      services: [
        { id: "sonarr", status: "ok" },
        { id: "radarr", status: "Down" },
      ],
    });
    expect(out).toEqual({ sonarr: "ok", radarr: "down" });
  });

  it("reads a flat object map", () => {
    const out = readServiceStatuses({
      sonarr: { status: "OK" },
      radarr: { status: "down" },
    });
    expect(out).toEqual({ sonarr: "ok", radarr: "down" });
  });

  it("falls back to a global status when nothing else matches", () => {
    expect(readServiceStatuses({ status: "Degraded" })).toEqual({
      controller: "degraded",
    });
  });
});

describe("statusMatchesCondition", () => {
  it("matches down on common down statuses", () => {
    expect(statusMatchesCondition("down", "down")).toBe(true);
    expect(statusMatchesCondition("error", "down")).toBe(true);
    expect(statusMatchesCondition("ok", "down")).toBe(false);
  });
  it("matches degraded on warn-flavoured statuses", () => {
    expect(statusMatchesCondition("degraded", "degraded")).toBe(true);
    expect(statusMatchesCondition("warn", "degraded")).toBe(true);
  });
  it("matches `any` on anything that isn't ok / healthy", () => {
    expect(statusMatchesCondition("ok", "any")).toBe(false);
    expect(statusMatchesCondition("healthy", "any")).toBe(false);
    expect(statusMatchesCondition("foo", "any")).toBe(true);
  });
});

describe("evaluateRulesOnce", () => {
  it("fires once threshold is hit and not again until recovery", () => {
    const emit = vi.fn();
    const state = {
      consecutive: new Map<string, Map<string, number>>(),
      fired: new Map<string, Set<string>>(),
    };
    const rules = [makeRule({ id: "r1", threshold: 2, service: "sonarr" })];
    const downHealth = { sonarr: { status: "down" } };
    const okHealth = { sonarr: { status: "ok" } };

    evaluateRulesOnce(rules, downHealth, state, emit);
    expect(emit).not.toHaveBeenCalled();
    evaluateRulesOnce(rules, downHealth, state, emit);
    expect(emit).toHaveBeenCalledTimes(1);
    evaluateRulesOnce(rules, downHealth, state, emit);
    expect(emit).toHaveBeenCalledTimes(1); // already fired, stays quiet

    evaluateRulesOnce(rules, okHealth, state, emit);
    // After recovery, threshold-hit cycle should re-arm.
    evaluateRulesOnce(rules, downHealth, state, emit);
    evaluateRulesOnce(rules, downHealth, state, emit);
    expect(emit).toHaveBeenCalledTimes(2);
  });

  it("matches `*` against every known service", () => {
    const emit = vi.fn();
    const state = {
      consecutive: new Map<string, Map<string, number>>(),
      fired: new Map<string, Set<string>>(),
    };
    const rules = [makeRule({ id: "r2", service: "*", threshold: 1 })];
    evaluateRulesOnce(
      rules,
      { sonarr: { status: "down" }, radarr: { status: "ok" } },
      state,
      emit,
    );
    expect(emit).toHaveBeenCalledTimes(1);
    expect(emit.mock.calls[0]?.[0]).toContain("sonarr");
  });
});

describe("startAlertEngine", () => {
  it("ticks immediately and on the interval, then stops cleanly", () => {
    window.localStorage.setItem(
      ALERT_RULES_STORAGE_KEY,
      JSON.stringify([makeRule({ threshold: 1 })]),
    );
    const emit = vi.fn();
    const pollHealth = vi.fn(() => ({ sonarr: { status: "down" } }));
    const handle = startAlertEngine({
      pollHealth,
      intervalMs: 100,
      emit,
    });
    // Ran once on start.
    expect(pollHealth).toHaveBeenCalledTimes(1);
    expect(emit).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(250);
    expect(pollHealth.mock.calls.length).toBeGreaterThanOrEqual(3);

    handle.stop();
    const callsAtStop = pollHealth.mock.calls.length;
    vi.advanceTimersByTime(500);
    expect(pollHealth.mock.calls.length).toBe(callsAtStop);
    // Stopping twice is safe.
    handle.stop();
  });

  it("skips polling when no rules are configured", () => {
    const emit = vi.fn();
    const pollHealth = vi.fn(() => ({ sonarr: { status: "down" } }));
    const handle = startAlertEngine({
      pollHealth,
      intervalMs: 50,
      emit,
    });
    // Even the initial tick is a no-op when rules.length === 0.
    expect(pollHealth).not.toHaveBeenCalled();
    vi.advanceTimersByTime(200);
    expect(pollHealth).not.toHaveBeenCalled();
    handle.stop();
  });
});
