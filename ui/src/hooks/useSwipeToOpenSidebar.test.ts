import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useSwipeToOpenSidebar } from "./useSwipeToOpenSidebar";

interface MockTouch {
  clientX: number;
  clientY: number;
}

function makeTouchEvent(
  touches: MockTouch[],
  changedTouches: MockTouch[] = touches,
): TouchEvent {
  return {
    touches,
    changedTouches,
  } as unknown as TouchEvent;
}

describe("useSwipeToOpenSidebar", () => {
  let now = 1_000_000;
  beforeEach(() => {
    now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function advance(ms: number): void {
    now += ms;
  }

  it("fires onOpen for a fast left-edge swipe past triggerDistance", () => {
    const onOpen = vi.fn();
    const { result } = renderHook(() =>
      useSwipeToOpenSidebar({
        onOpen,
        edgeWidth: 24,
        triggerDistance: 40,
        minVelocity: 0.3,
        enabled: true,
      }),
    );
    act(() => {
      result.current.onTouchStart(
        makeTouchEvent([{ clientX: 5, clientY: 200 }]),
      );
    });
    advance(100);
    act(() => {
      result.current.onTouchMove(
        makeTouchEvent([{ clientX: 80, clientY: 205 }]),
      );
      result.current.onTouchEnd(
        makeTouchEvent([], [{ clientX: 80, clientY: 205 }]),
      );
    });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("does not fire when the swipe starts away from the left edge", () => {
    const onOpen = vi.fn();
    const { result } = renderHook(() =>
      useSwipeToOpenSidebar({ onOpen, enabled: true }),
    );
    act(() => {
      result.current.onTouchStart(
        makeTouchEvent([{ clientX: 200, clientY: 200 }]),
      );
    });
    advance(100);
    act(() => {
      result.current.onTouchEnd(
        makeTouchEvent([], [{ clientX: 400, clientY: 205 }]),
      );
    });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("does not fire when the horizontal travel is below triggerDistance", () => {
    const onOpen = vi.fn();
    const { result } = renderHook(() =>
      useSwipeToOpenSidebar({
        onOpen,
        triggerDistance: 40,
        enabled: true,
      }),
    );
    act(() => {
      result.current.onTouchStart(
        makeTouchEvent([{ clientX: 5, clientY: 200 }]),
      );
    });
    advance(100);
    act(() => {
      result.current.onTouchEnd(
        makeTouchEvent([], [{ clientX: 30, clientY: 200 }]),
      );
    });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("does not fire on a slow swipe (velocity below minVelocity)", () => {
    const onOpen = vi.fn();
    const { result } = renderHook(() =>
      useSwipeToOpenSidebar({
        onOpen,
        triggerDistance: 40,
        minVelocity: 0.3,
        enabled: true,
      }),
    );
    act(() => {
      result.current.onTouchStart(
        makeTouchEvent([{ clientX: 5, clientY: 200 }]),
      );
    });
    // 80px over 1000ms => 0.08 px/ms < 0.3.
    advance(1000);
    act(() => {
      result.current.onTouchEnd(
        makeTouchEvent([], [{ clientX: 85, clientY: 205 }]),
      );
    });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("aborts when the gesture is mostly vertical", () => {
    const onOpen = vi.fn();
    const { result } = renderHook(() =>
      useSwipeToOpenSidebar({ onOpen, enabled: true }),
    );
    act(() => {
      result.current.onTouchStart(
        makeTouchEvent([{ clientX: 5, clientY: 200 }]),
      );
      // Move shows up as mostly vertical => aborts
      result.current.onTouchMove(
        makeTouchEvent([{ clientX: 25, clientY: 400 }]),
      );
    });
    advance(50);
    act(() => {
      result.current.onTouchEnd(
        makeTouchEvent([], [{ clientX: 100, clientY: 410 }]),
      );
    });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("does nothing when explicitly disabled", () => {
    const onOpen = vi.fn();
    const { result } = renderHook(() =>
      useSwipeToOpenSidebar({ onOpen, enabled: false }),
    );
    act(() => {
      result.current.onTouchStart(
        makeTouchEvent([{ clientX: 5, clientY: 200 }]),
      );
    });
    advance(100);
    act(() => {
      result.current.onTouchEnd(
        makeTouchEvent([], [{ clientX: 80, clientY: 205 }]),
      );
    });
    expect(onOpen).not.toHaveBeenCalled();
  });
});
