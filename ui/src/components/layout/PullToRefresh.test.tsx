import { describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { PullToRefresh } from "./PullToRefresh";

function fireTouch(
  el: Element,
  type: "touchstart" | "touchmove" | "touchend",
  clientY: number,
): void {
  const touch = { clientX: 0, clientY } as Touch;
  const event = new Event(type, { bubbles: true }) as TouchEvent;
  Object.defineProperty(event, "touches", {
    value: type === "touchend" ? [] : [touch],
  });
  Object.defineProperty(event, "changedTouches", { value: [touch] });
  el.dispatchEvent(event);
}

describe("PullToRefresh", () => {
  it("wraps children in a scrollable container", () => {
    render(
      <PullToRefresh onRefresh={() => undefined}>
        <div data-testid="child">payload</div>
      </PullToRefresh>,
    );
    const root = screen.getByTestId("pull-to-refresh");
    expect(root).toBeInTheDocument();
    expect(root.className).toContain("overflow-y-auto");
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });

  it("renders a hidden indicator slot at idle", () => {
    render(
      <PullToRefresh onRefresh={() => undefined}>
        <div>x</div>
      </PullToRefresh>,
    );
    const indicator = screen.getByTestId("pull-to-refresh-indicator");
    expect(indicator).toBeInTheDocument();
    expect(indicator).toHaveAttribute("aria-hidden", "true");
    expect(indicator.getAttribute("data-refreshing")).toBe("false");
  });

  it("shows the spinner during refresh", async () => {
    let resolveRefresh: (() => void) | null = null;
    const refreshPromise = new Promise<void>((resolve) => {
      resolveRefresh = resolve;
    });
    const onRefresh = vi.fn().mockImplementation(() => refreshPromise);
    const { container } = render(
      <PullToRefresh onRefresh={onRefresh} threshold={20} enabled>
        <div>x</div>
      </PullToRefresh>,
    );
    const root = container.querySelector(
      "[data-testid='pull-to-refresh']",
    ) as HTMLElement;
    Object.defineProperty(root, "scrollTop", { value: 0, configurable: true });

    await act(async () => {
      fireTouch(root, "touchstart", 100);
      fireTouch(root, "touchmove", 400);
      fireTouch(root, "touchend", 400);
      await Promise.resolve();
      await Promise.resolve();
    });
    // The hook flips the refreshing flag asynchronously inside its
    // touchend handler, so wait for the data-attribute to settle.
    await waitFor(() =>
      expect(
        screen
          .getByTestId("pull-to-refresh-indicator")
          .getAttribute("data-refreshing"),
      ).toBe("true"),
    );
    expect(onRefresh).toHaveBeenCalledTimes(1);

    // Resolve so the finally block clears the spinner before teardown.
    await act(async () => {
      resolveRefresh?.();
      await refreshPromise;
      await Promise.resolve();
    });
    expect(
      screen.getByTestId("pull-to-refresh-indicator").getAttribute(
        "data-refreshing",
      ),
    ).toBe("false");
  });
});
