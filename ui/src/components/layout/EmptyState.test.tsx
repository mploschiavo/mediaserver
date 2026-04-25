import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { Inbox } from "lucide-react";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("renders the title", () => {
    render(<EmptyState title="Nothing here" />);
    expect(
      screen.getByRole("heading", { name: "Nothing here" }),
    ).toBeInTheDocument();
  });

  it("renders the description when provided", () => {
    render(<EmptyState title="t" description="No items yet." />);
    expect(screen.getByText("No items yet.")).toBeInTheDocument();
  });

  it("does not render the description block when omitted", () => {
    const { container } = render(<EmptyState title="t" />);
    expect(container.querySelector("p")).toBeNull();
  });

  it("renders the icon-circle when an icon is provided", () => {
    const { container } = render(<EmptyState title="t" icon={Inbox} />);
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("does not render the icon-circle without an icon prop", () => {
    const { container } = render(<EmptyState title="t" />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("renders an action node and dispatches its click handler", async () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        title="t"
        action={
          <button type="button" onClick={onClick}>
            Do thing
          </button>
        }
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Do thing" }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("forwards className without dropping defaults", () => {
    const { container } = render(
      <EmptyState title="t" className="custom-marker" />,
    );
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("custom-marker");
    expect(root.className).toContain("border-dashed");
  });

  it("uses the dashed border + centered layout tokens", () => {
    const { container } = render(<EmptyState title="t" />);
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("rounded-lg");
    expect(root.className).toContain("text-center");
  });
});
