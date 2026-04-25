import { describe, expect, it } from "vitest";
import {
  extractTimestamp,
  hashSource,
  parseSearch,
  SOURCE_TONE_COUNT,
} from "./format";

describe("extractTimestamp", () => {
  it("pulls a SQL-ish ts out of a bracketed prefix", () => {
    const { ts, rest } = extractTimestamp(
      "[2026-04-07 12:00:01] INFO: boot ok",
    );
    expect(ts).toBe("2026-04-07 12:00:01");
    expect(rest).toBe("INFO: boot ok");
  });

  it("pulls an ISO ts with offset out of a bracketed prefix", () => {
    const { ts, rest } = extractTimestamp(
      "[2026-04-07T12:00:01+0000] WARN slow",
    );
    expect(ts).toBe("2026-04-07T12:00:01+0000");
    expect(rest).toBe("WARN slow");
  });

  it("returns null when no timestamp is present", () => {
    const { ts, rest } = extractTimestamp("just some message");
    expect(ts).toBeNull();
    expect(rest).toBe("just some message");
  });
});

describe("hashSource", () => {
  it("is stable: same name always returns same tone", () => {
    const a = hashSource("controller");
    const b = hashSource("controller");
    expect(a.fg).toBe(b.fg);
  });

  it("returns a tone from the documented palette", () => {
    expect(SOURCE_TONE_COUNT).toBe(8);
    const tone = hashSource("sonarr");
    expect(tone.fg).toMatch(/^oklch\(/);
  });

  it("distributes different names across tones", () => {
    // Probabilistic but deterministic: 8 distinct services should
    // not collapse to a single bucket; we expect at least 3 unique
    // colors out of the 8 standard sources.
    const names = [
      "controller",
      "sonarr",
      "radarr",
      "lidarr",
      "readarr",
      "bazarr",
      "prowlarr",
      "qbittorrent",
    ];
    const colors = new Set(names.map((n) => hashSource(n).fg));
    expect(colors.size).toBeGreaterThanOrEqual(3);
  });
});

describe("parseSearch", () => {
  it("returns no-op predicate for empty input", () => {
    const p = parseSearch("");
    expect(p.active).toBe(false);
    expect(p.test("anything")).toBe(true);
    expect(p.split("hello")).toEqual([{ text: "hello", match: false }]);
  });

  it("substring is case-insensitive by default", () => {
    const p = parseSearch("BoOt");
    expect(p.active).toBe(true);
    expect(p.test("system boot ok")).toBe(true);
    expect(p.test("system shutdown")).toBe(false);
  });

  it("substring split highlights all matches", () => {
    const p = parseSearch("foo");
    const segs = p.split("foo and foo");
    expect(segs.filter((s) => s.match).length).toBe(2);
    expect(segs.filter((s) => s.match).every((s) => s.text === "foo")).toBe(
      true,
    );
  });

  it("/regex/ form is honoured (case-sensitive by default)", () => {
    const p = parseSearch("/Boot/");
    expect(p.active).toBe(true);
    expect(p.test("Boot complete")).toBe(true);
    expect(p.test("boot complete")).toBe(false);
  });

  it("/regex/i form sets the case-insensitive flag", () => {
    const p = parseSearch("/boot/i");
    expect(p.test("BOOT complete")).toBe(true);
  });

  it("falls back to substring on an invalid regex", () => {
    // Unbalanced bracket — `new RegExp` throws; we should still find
    // the literal "foo[" via substring.
    const p = parseSearch("/foo[/");
    expect(p.active).toBe(true);
    // Both halves of the body get tried before fallback; verify the
    // search still functions on a matching literal.
    expect(p.test("the foo[ token")).toBe(true);
  });

  it("/regex without closing slash still parses", () => {
    const p = parseSearch("/error");
    expect(p.test("error somewhere")).toBe(true);
  });
});
