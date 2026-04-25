import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";

import {
  ALERT_RULES_STORAGE_KEY,
  getAlertRules,
  newAlertRuleId,
  subscribeAlertRules,
  useAlertRules,
  type AlertRule,
} from "./hooks";

function makeRule(overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    id: overrides.id ?? "rule-1",
    name: overrides.name ?? "Sonarr down",
    service: overrides.service ?? "sonarr",
    condition: overrides.condition ?? "down",
    threshold: overrides.threshold ?? 3,
    action: overrides.action ?? "toast",
  };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("alerts feature hooks", () => {
  it("returns an empty list when storage is empty", () => {
    const { result } = renderHook(() => useAlertRules());
    expect(result.current.rules).toEqual([]);
  });

  it("hydrates the initial rule list from localStorage", () => {
    window.localStorage.setItem(
      ALERT_RULES_STORAGE_KEY,
      JSON.stringify([makeRule()]),
    );
    const { result } = renderHook(() => useAlertRules());
    expect(result.current.rules).toHaveLength(1);
    expect(result.current.rules[0]?.name).toBe("Sonarr down");
  });

  it("ignores malformed entries in storage", () => {
    window.localStorage.setItem(
      ALERT_RULES_STORAGE_KEY,
      JSON.stringify([{ junk: true }, makeRule()]),
    );
    const { result } = renderHook(() => useAlertRules());
    expect(result.current.rules).toHaveLength(1);
  });

  it("ignores corrupt JSON in storage and resets to empty", () => {
    window.localStorage.setItem(ALERT_RULES_STORAGE_KEY, "not-json");
    const { result } = renderHook(() => useAlertRules());
    expect(result.current.rules).toEqual([]);
  });

  it("save() appends a new rule and writes to storage", () => {
    const { result } = renderHook(() => useAlertRules());
    act(() => result.current.save(makeRule()));
    expect(result.current.rules).toHaveLength(1);
    const raw = window.localStorage.getItem(ALERT_RULES_STORAGE_KEY);
    expect(raw && JSON.parse(raw)).toHaveLength(1);
  });

  it("save() replaces an existing rule by id", () => {
    const { result } = renderHook(() => useAlertRules());
    act(() => result.current.save(makeRule({ id: "x", name: "first" })));
    act(() => result.current.save(makeRule({ id: "x", name: "second" })));
    expect(result.current.rules).toHaveLength(1);
    expect(result.current.rules[0]?.name).toBe("second");
  });

  it("remove() drops the rule by id", () => {
    const { result } = renderHook(() => useAlertRules());
    act(() => result.current.save(makeRule({ id: "a" })));
    act(() => result.current.save(makeRule({ id: "b", name: "second" })));
    act(() => result.current.remove("a"));
    expect(result.current.rules).toHaveLength(1);
    expect(result.current.rules[0]?.id).toBe("b");
  });

  it("subscribeAlertRules notifies after a save", () => {
    const seen: number[] = [];
    const unsub = subscribeAlertRules((rs) => seen.push(rs.length));
    const { result } = renderHook(() => useAlertRules());
    act(() => result.current.save(makeRule()));
    unsub();
    expect(seen.length).toBeGreaterThan(0);
    expect(seen[seen.length - 1]).toBe(1);
  });

  it("getAlertRules() reads the current snapshot", () => {
    expect(getAlertRules()).toEqual([]);
    window.localStorage.setItem(
      ALERT_RULES_STORAGE_KEY,
      JSON.stringify([makeRule()]),
    );
    expect(getAlertRules()).toHaveLength(1);
  });

  it("newAlertRuleId returns distinct strings", () => {
    const a = newAlertRuleId();
    const b = newAlertRuleId();
    expect(a).not.toBe(b);
    expect(typeof a).toBe("string");
  });
});
