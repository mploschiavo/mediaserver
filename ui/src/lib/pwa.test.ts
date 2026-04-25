import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

interface CapturedOpts {
  onNeedRefresh?: () => void;
  onOfflineReady?: () => void;
}

interface PwaTestGlobals {
  __pwaOpts?: CapturedOpts;
  __pwaUpdater?: ReturnType<typeof vi.fn>;
}

const g = globalThis as unknown as PwaTestGlobals;

vi.mock("virtual:pwa-register", () => ({
  registerSW: vi.fn((opts?: CapturedOpts) => {
    // Stash the callback so the test can fire it manually.
    g.__pwaOpts = opts;
    const updater = vi.fn(async () => {});
    g.__pwaUpdater = updater;
    return updater;
  }),
}));

describe("pwa", () => {
  beforeEach(() => {
    vi.resetModules();
    g.__pwaOpts = undefined;
    g.__pwaUpdater = undefined;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("initPwa is a no-op when window is undefined", async () => {
    // Save and remove the global window so the SSR guard fires.
    const originalWindow = (
      globalThis as unknown as { window?: unknown }
    ).window;
    delete (globalThis as { window?: unknown }).window;
    try {
      const mod = await import("./pwa");
      mod.initPwa(() => {});
      expect(g.__pwaOpts).toBeUndefined();
      expect(g.__pwaUpdater).toBeUndefined();
    } finally {
      (globalThis as unknown as { window?: unknown }).window = originalWindow;
    }
  });

  it("usePwaUpdate returns hasUpdate=false initially", async () => {
    const { usePwaUpdate } = await import("./pwa");
    const { result } = renderHook(() => usePwaUpdate());
    expect(result.current.hasUpdate).toBe(false);
    expect(typeof result.current.apply).toBe("function");
  });

  it("onNeedRefresh flips hasUpdate=true and apply() invokes the registered updater", async () => {
    const { usePwaUpdate } = await import("./pwa");
    const { result } = renderHook(() => usePwaUpdate());

    expect(g.__pwaOpts?.onNeedRefresh).toBeTypeOf("function");

    act(() => {
      g.__pwaOpts?.onNeedRefresh?.();
    });

    expect(result.current.hasUpdate).toBe(true);

    act(() => {
      result.current.apply();
    });

    expect(g.__pwaUpdater).toHaveBeenCalledWith(true);
  });

  it("onOfflineReady is wired and safe to invoke", async () => {
    const { initPwa } = await import("./pwa");
    initPwa(() => {});
    expect(() => g.__pwaOpts?.onOfflineReady?.()).not.toThrow();
  });
});
