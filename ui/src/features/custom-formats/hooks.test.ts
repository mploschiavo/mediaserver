import { describe, expect, it } from "vitest";
import { readFormats } from "./hooks";

describe("readFormats", () => {
  it("returns [] for undefined", () => {
    expect(readFormats(undefined)).toEqual([]);
  });

  it("returns the wrapped formats array", () => {
    const out = readFormats({
      formats: [
        { id: 1, name: "A" },
        { id: 2, name: "B" },
      ],
    });
    expect(out).toHaveLength(2);
    expect(out[0]?.name).toBe("A");
  });

  it("returns [] when `formats` is missing or non-array", () => {
    expect(readFormats({})).toEqual([]);
    expect(readFormats({ formats: "nope" as unknown as never })).toEqual([]);
  });

  it("returns an empty list when both `formats` and the bare array are absent", () => {
    // payload is a plain object with neither shape: nothing to iterate.
    expect(readFormats({ noise: 1 })).toEqual([]);
  });
});
