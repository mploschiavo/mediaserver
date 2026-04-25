import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The keyboard helpers branch on `navigator.platform` to decide
 * whether to render ⌘ glyphs or Ctrl text. We can't mutate the
 * real navigator, so each test resets module state and stubs the
 * global before importing the module under test.
 */

const importFresh = async () =>
  (await import("./keyboard")) as typeof import("./keyboard");

function stubNavigator(platform: string): void {
  vi.stubGlobal("navigator", {
    platform,
    userAgent: `test-${platform}`,
  } as Navigator);
}

describe("formatShortcut + platformShortcut", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders mod+k as ⌘K on macOS", async () => {
    stubNavigator("MacIntel");
    const { formatShortcut } = await importFresh();
    expect(formatShortcut("mod+k")).toBe("⌘K");
  });

  it("renders mod+k as Ctrl+K on Linux/Windows", async () => {
    stubNavigator("Linux x86_64");
    const { formatShortcut } = await importFresh();
    expect(formatShortcut("mod+k")).toBe("Ctrl+K");
  });

  it("formats sequence shortcuts (g m) by preserving the space", async () => {
    stubNavigator("MacIntel");
    const { formatShortcut } = await importFresh();
    expect(formatShortcut("g m")).toBe("G M");
  });

  it("uppercases single-letter tokens", async () => {
    stubNavigator("Linux x86_64");
    const { formatShortcut } = await importFresh();
    expect(formatShortcut("ctrl+k")).toBe("Ctrl+K");
  });

  it("maps named keys to glyphs", async () => {
    stubNavigator("MacIntel");
    const { formatShortcut } = await importFresh();
    expect(formatShortcut("shift+enter")).toBe("⇧⏎");
    expect(formatShortcut("alt+up")).toBe("⌥↑");
    expect(formatShortcut("esc")).toBe("Esc");
  });

  it("falls back to original token for unknown multi-letter keys", async () => {
    stubNavigator("Linux x86_64");
    const { formatShortcut } = await importFresh();
    expect(formatShortcut("foo")).toBe("foo");
  });

  it("platformShortcut translates 'mod' to 'meta' on Mac", async () => {
    stubNavigator("MacIntel");
    const { platformShortcut } = await importFresh();
    expect(platformShortcut("mod+k")).toBe("meta+k");
  });

  it("platformShortcut translates 'mod' to 'ctrl' off Mac", async () => {
    stubNavigator("Linux x86_64");
    const { platformShortcut } = await importFresh();
    expect(platformShortcut("mod+k")).toBe("ctrl+k");
  });

  it("platformShortcut leaves other tokens untouched", async () => {
    stubNavigator("MacIntel");
    const { platformShortcut } = await importFresh();
    expect(platformShortcut("shift+a")).toBe("shift+a");
  });

  it("re-exports the Kbd component", async () => {
    const mod = await importFresh();
    expect(mod.Kbd).toBeDefined();
  });
});
