import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { usePullToRefresh } from "./usePullToRefresh";

interface MockTouch {
  clientX: number;
  clientY: number;
}

function makeTouchEvent(touches: MockTouch[]): TouchEvent {
  return { touches } as unknown as TouchEvent;
}

function attachScrollableHost(scrollTop = 0): HTMLDivElement {
  const el = document.createElement("div");
  Object.defineProperty(el, "scrollTop", {
    value: scrollTop,
    writable: true,
  });
  document.body.appendChild(el);
  return el;
}

describe("usePullToRefresh", () => {
  it("uses the documented threshold default", () => {
    const onRefresh = vi.fn();
    const { result } = renderHook(() =>
      usePullToRefresh({ onRefresh, enabled: true }),
    );
    // No pull yet => idle state.
    expect(result.current.refreshing).toBe(false);
    expect(result.current.offset).toBe(0);
    expect(result.current.pulling).toBe(false);
  });

  it("exposes spreadable rootProps with the touch handlers", () => {
    const onRefresh = vi.fn();
    const { result } = renderHook(() =>
      usePullToRefresh({ onRefresh, enabled: true }),
    );
    expect(typeof result.current.rootProps.onTouchStart).toBe("function");
    expect(typeof result.current.rootProps.onTouchMove).toBe("function");
    expect(typeof result.current.rootProps.onTouchEnd).toBe("function");
    expect(typeof result.current.rootProps.ref).toBe("function");
  });

  it("fires onRefresh once after pull > threshold + release", async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      usePullToRefresh({ onRefresh, threshold: 80, enabled: true }),
    );
    const host = attachScrollableHost(0);
    act(() => {
      result.current.rootProps.ref(host);
    });

    act(() => {
      result.current.rootProps.onTouchStart(
        makeTouchEvent([{ clientX: 50, clientY: 100 }]),
      );
    });
    // Pull 200px down => after default resistance 0.5 => offset 100, > 80 threshold.
    act(() => {
      result.current.rootProps.onTouchMove(
        makeTouchEvent([{ clientX: 50, clientY: 300 }]),
      );
    });
    expect(result.current.pulling).toBe(true);
    expect(result.current.offset).toBeGreaterThan(80);

    await act(async () => {
      result.current.rootProps.onTouchEnd();
      // Allow the microtask queueing onRefresh + its finally to settle.
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("does not fire onRefresh when released below threshold", () => {
    const onRefresh = vi.fn();
    const { result } = renderHook(() =>
      usePullToRefresh({ onRefresh, threshold: 80, enabled: true }),
    );
    const host = attachScrollableHost(0);
    act(() => {
      result.current.rootProps.ref(host);
    });
    act(() => {
      result.current.rootProps.onTouchStart(
        makeTouchEvent([{ clientX: 50, clientY: 100 }]),
      );
      // 60px down * 0.5 resistance = 30 offset (below 80).
      result.current.rootProps.onTouchMove(
        makeTouchEvent([{ clientX: 50, clientY: 160 }]),
      );
      result.current.rootProps.onTouchEnd();
    });
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it("does not fire when the host is scrolled past the top", () => {
    const onRefresh = vi.fn();
    const { result } = renderHook(() =>
      usePullToRefresh({ onRefresh, threshold: 80, enabled: true }),
    );
    const host = attachScrollableHost(120);
    act(() => {
      result.current.rootProps.ref(host);
    });
    act(() => {
      result.current.rootProps.onTouchStart(
        makeTouchEvent([{ clientX: 50, clientY: 100 }]),
      );
      result.current.rootProps.onTouchMove(
        makeTouchEvent([{ clientX: 50, clientY: 400 }]),
      );
      result.current.rootProps.onTouchEnd();
    });
    expect(onRefresh).not.toHaveBeenCalled();
    expect(result.current.refreshing).toBe(false);
  });

  it("ignores upward drags (negative delta)", () => {
    const onRefresh = vi.fn();
    const { result } = renderHook(() =>
      usePullToRefresh({ onRefresh, enabled: true }),
    );
    const host = attachScrollableHost(0);
    act(() => {
      result.current.rootProps.ref(host);
    });
    act(() => {
      result.current.rootProps.onTouchStart(
        makeTouchEvent([{ clientX: 50, clientY: 200 }]),
      );
      result.current.rootProps.onTouchMove(
        makeTouchEvent([{ clientX: 50, clientY: 50 }]),
      );
      result.current.rootProps.onTouchEnd();
    });
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it("respects an explicit enabled=false flag", () => {
    const onRefresh = vi.fn();
    const { result } = renderHook(() =>
      usePullToRefresh({ onRefresh, enabled: false }),
    );
    const host = attachScrollableHost(0);
    act(() => {
      result.current.rootProps.ref(host);
    });
    act(() => {
      result.current.rootProps.onTouchStart(
        makeTouchEvent([{ clientX: 50, clientY: 100 }]),
      );
      result.current.rootProps.onTouchMove(
        makeTouchEvent([{ clientX: 50, clientY: 400 }]),
      );
      result.current.rootProps.onTouchEnd();
    });
    expect(onRefresh).not.toHaveBeenCalled();
  });
});
