import { describe, expect, it } from "vitest";
import { formatRelative, statusLabel, statusVariant } from "./format";

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
});
