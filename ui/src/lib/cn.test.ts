import { describe, expect, it } from "vitest";
import { cn } from "./cn";

describe("cn()", () => {
  it("merges two simple class strings", () => {
    expect(cn("a", "b")).toBe("a b");
  });

  it("dedupes Tailwind conflicts via twMerge (last wins)", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
  });

  it("dedupes background utilities", () => {
    expect(cn("bg-red-500", "bg-blue-500")).toBe("bg-blue-500");
  });

  it("ignores falsy values (false, null, undefined)", () => {
    expect(cn("a", false, null, undefined, "b")).toBe("a b");
  });

  it("treats empty string as no-op", () => {
    expect(cn("a", "", "b")).toBe("a b");
  });

  it("accepts conditional objects", () => {
    expect(cn({ on: true, off: false })).toBe("on");
  });

  it("accepts arrays", () => {
    expect(cn(["a", "b"], "c")).toBe("a b c");
  });

  it("accepts deeply nested arrays + objects", () => {
    expect(cn(["a", ["b", { c: true, d: false }]])).toBe("a b c");
  });

  it("returns an empty string when given nothing useful", () => {
    expect(cn()).toBe("");
    expect(cn(false, null, undefined)).toBe("");
  });

  it("preserves non-conflicting Tailwind classes", () => {
    expect(cn("p-2 mx-4", "rounded-md")).toContain("p-2");
    expect(cn("p-2 mx-4", "rounded-md")).toContain("mx-4");
    expect(cn("p-2 mx-4", "rounded-md")).toContain("rounded-md");
  });

  it("twMerge resolves color modifier conflicts", () => {
    expect(cn("text-sm text-red-500", "text-blue-500")).toContain(
      "text-blue-500",
    );
    expect(cn("text-sm text-red-500", "text-blue-500")).not.toContain(
      "text-red-500",
    );
  });
});
