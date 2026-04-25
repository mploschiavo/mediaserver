import { describe, expect, it } from "vitest";
import {
  formatElapsed,
  epochToIso,
  formatAbsolute,
  formatUntil,
  nextCronFire,
} from "./format";

describe("formatElapsed", () => {
  it("renders sub-second values in milliseconds", () => {
    expect(formatElapsed(0.045)).toBe("45ms");
  });
  it("renders seconds with 1 decimal under a minute", () => {
    expect(formatElapsed(12.34)).toBe("12.3s");
  });
  it("renders minutes + seconds under an hour", () => {
    expect(formatElapsed(75)).toBe("1m 15s");
  });
  it("renders hours + minutes for ≥ 3600s", () => {
    expect(formatElapsed(3700)).toBe("1h 01m");
  });
  it("returns the em-dash sentinel for missing/invalid input", () => {
    expect(formatElapsed(null)).toBe("—");
    expect(formatElapsed(undefined)).toBe("—");
    expect(formatElapsed(0)).toBe("—");
    expect(formatElapsed(NaN)).toBe("—");
  });
});

describe("epochToIso", () => {
  it("produces an ISO-8601 string for valid input", () => {
    const iso = epochToIso(1_700_000_000);
    expect(iso).toBe(new Date(1_700_000_000_000).toISOString());
  });
  it("returns empty string for missing input", () => {
    expect(epochToIso(null)).toBe("");
    expect(epochToIso(undefined)).toBe("");
    expect(epochToIso(0)).toBe("");
  });
});

describe("formatAbsolute", () => {
  it("returns a non-empty locale string for valid input", () => {
    const out = formatAbsolute(1_700_000_000);
    expect(out.length).toBeGreaterThan(0);
  });
  it("returns empty string for invalid input", () => {
    expect(formatAbsolute(null)).toBe("");
  });
});

describe("nextCronFire", () => {
  it("returns null for non-cron strings", () => {
    expect(nextCronFire("", new Date())).toBeNull();
    expect(nextCronFire("not-a-cron", new Date())).toBeNull();
  });

  it("returns null for shapes other than `m h * * *`", () => {
    // 4 fields — not 5
    expect(nextCronFire("0 * * *", new Date())).toBeNull();
    // dom not '*'
    expect(nextCronFire("0 12 1 * *", new Date())).toBeNull();
    // dow not '*'
    expect(nextCronFire("0 12 * * 1", new Date())).toBeNull();
    // mon not '*'
    expect(nextCronFire("0 12 * 1 *", new Date())).toBeNull();
    // ranges / lists are not supported
    expect(nextCronFire("0,30 * * * *", new Date())).toBeNull();
    expect(nextCronFire("0-5 * * * *", new Date())).toBeNull();
  });

  it("computes the next fire for a fixed minute + hour expression", () => {
    // "0 12 * * *" — every day at noon. From 11:30 the next fire is
    // the same day at noon.
    const now = new Date(2026, 3, 24, 11, 30, 0, 0);
    const next = nextCronFire("0 12 * * *", now);
    expect(next).not.toBeNull();
    expect(next?.getHours()).toBe(12);
    expect(next?.getMinutes()).toBe(0);
    expect(next?.getDate()).toBe(24);
  });

  it("handles step expressions (`*/6`) correctly", () => {
    // "0 */6 * * *" — fires at 00, 06, 12, 18. From 13:00 the next
    // fire is the same day at 18:00.
    const now = new Date(2026, 3, 24, 13, 0, 0, 0);
    const next = nextCronFire("0 */6 * * *", now);
    expect(next).not.toBeNull();
    expect(next?.getHours()).toBe(18);
    expect(next?.getMinutes()).toBe(0);
  });

  it("rolls forward to the next day when no fires remain today", () => {
    // "15 */6 * * *" — fires at 00:15, 06:15, 12:15, 18:15.
    // From 19:00 the next fire is tomorrow at 00:15.
    const now = new Date(2026, 3, 24, 19, 0, 0, 0);
    const next = nextCronFire("15 */6 * * *", now);
    expect(next).not.toBeNull();
    expect(next?.getDate()).toBe(25);
    expect(next?.getHours()).toBe(0);
    expect(next?.getMinutes()).toBe(15);
  });

  it("treats `*` minutes as every minute", () => {
    // "* * * * *" technically — every minute. From 12:34:30 the
    // next fire is 12:35:00.
    const now = new Date(2026, 3, 24, 12, 34, 30, 0);
    const next = nextCronFire("* * * * *", now);
    expect(next).not.toBeNull();
    expect(next?.getHours()).toBe(12);
    expect(next?.getMinutes()).toBe(35);
  });
});

describe("formatUntil", () => {
  it("returns em-dash for null target", () => {
    expect(formatUntil(null)).toBe("—");
  });
  it('says "imminently" for past or current timestamps', () => {
    const past = new Date(Date.now() - 1000);
    expect(formatUntil(past)).toBe("imminently");
  });
  it("phrases sub-minute deltas in seconds", () => {
    const now = Date.now();
    const target = new Date(now + 30_000);
    expect(formatUntil(target, now)).toMatch(/^in \d+s$/);
  });
  it("phrases sub-hour deltas in minutes", () => {
    const now = Date.now();
    const target = new Date(now + 12 * 60_000);
    expect(formatUntil(target, now)).toBe("in 12m");
  });
  it("phrases sub-day deltas as `Xh Ym`", () => {
    const now = Date.now();
    const target = new Date(now + 2 * 3600_000 + 14 * 60_000);
    expect(formatUntil(target, now)).toBe("in 2h 14m");
  });
});
