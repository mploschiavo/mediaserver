import { describe, expect, it } from "vitest";
import { formatBytes, formatRelative } from "./format";

describe("formatBytes", () => {
  it("returns 0 B for zero", () => {
    expect(formatBytes(0)).toBe("0 B");
  });

  it("returns 0 B for negative input", () => {
    expect(formatBytes(-100)).toBe("0 B");
  });

  it("returns 0 B for non-finite input", () => {
    expect(formatBytes(NaN)).toBe("0 B");
    expect(formatBytes(Infinity)).toBe("0 B");
  });

  it("renders raw bytes under 1 KB", () => {
    // <1024 keeps the bytes unit; the toFixed(1) branch applies.
    expect(formatBytes(512)).toBe("512.0 B");
  });

  it("renders 1024 as 1.00 KB (binary prefixes, two decimals under 10)", () => {
    expect(formatBytes(1024)).toBe("1.00 KB");
  });

  it("uses one decimal in the 10–100 range", () => {
    expect(formatBytes(50 * 1024)).toBe("50.0 KB");
  });

  it("rounds to integer at >= 100 in a unit", () => {
    expect(formatBytes(500 * 1024)).toBe("500 KB");
  });

  it("steps up units at the 1024 threshold", () => {
    expect(formatBytes(1024 * 1024)).toBe("1.00 MB");
    expect(formatBytes(1024 ** 3)).toBe("1.00 GB");
    expect(formatBytes(1024 ** 4)).toBe("1.00 TB");
  });

  it("clamps to PB for very large numbers", () => {
    // 1024^6 (EB) clamps to PB since the table only goes that far.
    expect(formatBytes(1024 ** 6).endsWith(" PB")).toBe(true);
  });
});

describe("formatRelative", () => {
  const NOW = Date.parse("2025-01-01T12:00:00Z");

  it("returns 'never' for empty input", () => {
    expect(formatRelative("")).toBe("never");
  });

  it("returns 'never' for unparseable input", () => {
    expect(formatRelative("not-a-date")).toBe("never");
  });

  it("returns 'just now' inside the 5-second window", () => {
    expect(formatRelative(new Date(NOW - 2_000).toISOString(), NOW)).toBe(
      "just now",
    );
  });

  it("renders seconds under a minute", () => {
    expect(formatRelative(new Date(NOW - 30_000).toISOString(), NOW)).toBe(
      "30s ago",
    );
  });

  it("renders minutes under an hour", () => {
    expect(formatRelative(new Date(NOW - 12 * 60_000).toISOString(), NOW)).toBe(
      "12m ago",
    );
  });

  it("renders hours under a day", () => {
    expect(
      formatRelative(new Date(NOW - 2 * 60 * 60_000).toISOString(), NOW),
    ).toBe("2h ago");
  });

  it("renders days otherwise", () => {
    expect(
      formatRelative(new Date(NOW - 3 * 24 * 60 * 60_000).toISOString(), NOW),
    ).toBe("3d ago");
  });

  it("clamps clock-skew (future timestamps) to 'just now'", () => {
    expect(formatRelative(new Date(NOW + 60_000).toISOString(), NOW)).toBe(
      "just now",
    );
  });
});
