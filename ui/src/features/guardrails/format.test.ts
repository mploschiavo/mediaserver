import { describe, expect, it } from "vitest";
import {
  formatCurrentValue,
  formatRelative,
  formatThreshold,
  statusLabel,
  statusVariant,
} from "./format";

describe("guardrails format helpers", () => {
  it("statusLabel humanises severities", () => {
    expect(statusLabel("ok")).toBe("OK");
    expect(statusLabel("warning")).toBe("Warning");
    expect(statusLabel("critical")).toBe("Critical");
    expect(statusLabel("disabled")).toBe("Disabled");
    expect(statusLabel(undefined)).toBe("Unknown");
  });

  it("statusVariant maps severities to badge variants", () => {
    expect(statusVariant("ok")).toBe("success");
    expect(statusVariant("warning")).toBe("warning");
    expect(statusVariant("critical")).toBe("danger");
    expect(statusVariant("disabled")).toBe("outline");
  });

  it("formatRelative returns 'never' for missing timestamps", () => {
    expect(formatRelative(undefined)).toBe("never");
    expect(formatRelative(0)).toBe("never");
  });

  it("formatRelative renders a minute window", () => {
    const ts = Date.now() / 1000 - 90; // 90s ago
    expect(formatRelative(ts)).toBe("1m ago");
  });

  it("formatCurrentValue renders bytes per mount as GiB", () => {
    // 580815425536 ≈ 541 GiB — matches a real /api/guardrails test response.
    const v = formatCurrentValue({
      config: 580815425536,
      media: 580815425536,
    });
    expect(v).toContain("config");
    expect(v).toContain("541");
    expect(v).toContain("GiB");
    expect(v).toContain("media");
  });

  it("formatCurrentValue handles primitives", () => {
    expect(formatCurrentValue(null)).toBe("—");
    expect(formatCurrentValue(undefined)).toBe("—");
    expect(formatCurrentValue(42)).toBe("42");
    expect(formatCurrentValue("ok")).toBe("ok");
    expect(formatCurrentValue(true)).toBe("true");
  });

  it("formatCurrentValue formats large bytes as GiB", () => {
    expect(formatCurrentValue(2 * 1024 * 1024 * 1024)).toContain("GiB");
  });

  it("formatThreshold renders key=value pairs", () => {
    expect(formatThreshold({ min_free_gib: 700 })).toBe("min_free_gib=700");
    expect(formatThreshold({})).toBe("{}");
  });
});
