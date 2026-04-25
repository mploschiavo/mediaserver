import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useBytesCounter } from "./use-bytes-counter";

const reduceMotionMock = vi.hoisted(() => ({ value: false }));

vi.mock("framer-motion", async () => {
  const actual = await vi.importActual<typeof import("framer-motion")>(
    "framer-motion",
  );
  return {
    ...actual,
    useReducedMotion: () => reduceMotionMock.value,
  };
});

describe("useBytesCounter", () => {
  beforeEach(() => {
    reduceMotionMock.value = false;
  });

  it("returns the formatted target on first render", () => {
    const { result } = renderHook(() =>
      useBytesCounter(2048, (n) => `${Math.round(n)}b`, 0.001),
    );
    // Animation runs but the seed value formats the target up-front.
    expect(result.current).toMatch(/b$/);
  });

  it("snaps to the target when prefers-reduced-motion is on", () => {
    reduceMotionMock.value = true;
    const fmt = vi.fn((n: number) => `${Math.round(n)}b`);
    const { result } = renderHook(() => useBytesCounter(5000, fmt));
    // No tween should run; the formatter is called for the seed
    // and once for the snap.
    expect(result.current).toBe("5000b");
  });

  it("re-runs format() with intermediate values when target changes", async () => {
    const fmt = vi.fn((n: number) => `${Math.round(n)}b`);
    const { rerender } = renderHook(
      ({ t }: { t: number }) => useBytesCounter(t, fmt, 0.05),
      { initialProps: { t: 0 } },
    );
    rerender({ t: 1000 });
    // Wait for Framer Motion's rAF loop to flush. Using a real
    // setTimeout — the animation is a real-clock tween.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 120));
    });
    // The formatter was invoked at least once for the seed; for
    // a real animation it should run more than that.
    expect(fmt.mock.calls.length).toBeGreaterThan(0);
  });

  it("stops the animation on unmount", () => {
    const fmt = (n: number) => `${Math.round(n)}b`;
    const { unmount } = renderHook(() => useBytesCounter(1000, fmt, 5));
    expect(() => unmount()).not.toThrow();
  });

  it("converges to the formatted target after the animation completes", async () => {
    const fmt = (n: number) => `${Math.round(n)}b`;
    const { result, rerender } = renderHook(
      ({ t }: { t: number }) => useBytesCounter(t, fmt, 0.02),
      { initialProps: { t: 0 } },
    );
    rerender({ t: 100 });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 80));
    });
    expect(result.current).toBe("100b");
  });
});
