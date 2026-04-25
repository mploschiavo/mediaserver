import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { Switch } from "./switch";

describe("Switch", () => {
  it("renders with the switch role", () => {
    render(<Switch aria-label="notifications" />);
    expect(screen.getByRole("switch")).toBeInTheDocument();
  });

  it("starts in the unchecked state by default", () => {
    render(<Switch aria-label="notifications" />);
    const el = screen.getByRole("switch");
    expect(el).toHaveAttribute("data-state", "unchecked");
    expect(el).toHaveAttribute("aria-checked", "false");
  });

  it("respects an initial checked state", () => {
    render(<Switch aria-label="notifications" defaultChecked />);
    const el = screen.getByRole("switch");
    expect(el).toHaveAttribute("data-state", "checked");
    expect(el).toHaveAttribute("aria-checked", "true");
  });

  it("toggles state on click and fires onCheckedChange", async () => {
    const onCheckedChange = vi.fn();
    render(
      <Switch aria-label="notifications" onCheckedChange={onCheckedChange} />,
    );
    const el = screen.getByRole("switch");
    await userEvent.click(el);
    expect(onCheckedChange).toHaveBeenCalledWith(true);
    expect(el).toHaveAttribute("data-state", "checked");
  });

  it("toggles via Space when focused", async () => {
    const onCheckedChange = vi.fn();
    render(
      <Switch aria-label="notifications" onCheckedChange={onCheckedChange} />,
    );
    const el = screen.getByRole("switch");
    el.focus();
    await userEvent.keyboard(" ");
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });

  it("respects the disabled prop and does not toggle", async () => {
    const onCheckedChange = vi.fn();
    render(
      <Switch
        aria-label="notifications"
        disabled
        onCheckedChange={onCheckedChange}
      />,
    );
    const el = screen.getByRole("switch");
    expect(el).toBeDisabled();
    await userEvent.click(el);
    expect(onCheckedChange).not.toHaveBeenCalled();
  });

  it("forwards className without dropping defaults", () => {
    render(<Switch aria-label="notifications" className="extra-marker" />);
    const el = screen.getByRole("switch");
    expect(el.className).toContain("extra-marker");
    expect(el.className).toContain("rounded-full");
  });

  it("forwards ref to the root element", () => {
    const ref = { current: null as HTMLButtonElement | null };
    render(<Switch ref={ref} aria-label="notifications" />);
    expect(ref.current).not.toBeNull();
    expect(ref.current?.getAttribute("role")).toBe("switch");
  });
});
