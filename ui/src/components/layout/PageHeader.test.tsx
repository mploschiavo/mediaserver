import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("renders the title as a level-1 heading", () => {
    render(<PageHeader title="Content" />);
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("Content");
  });

  it("renders the description when provided", () => {
    render(<PageHeader title="t" description="Manage media library." />);
    expect(screen.getByText("Manage media library.")).toBeInTheDocument();
  });

  it("does not render the description tag when omitted", () => {
    const { container } = render(<PageHeader title="t" />);
    expect(container.querySelector("p")).toBeNull();
  });

  it("renders actions on the right side", () => {
    render(
      <PageHeader
        title="t"
        actions={
          <>
            <button>One</button>
            <button>Two</button>
          </>
        }
      />,
    );
    expect(screen.getByRole("button", { name: "One" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Two" })).toBeInTheDocument();
  });

  it("does not render the actions wrapper when actions are omitted", () => {
    const { container } = render(<PageHeader title="t" />);
    // header > [title block]; no second flex action group
    expect(container.querySelectorAll("header > div").length).toBe(1);
  });

  it("forwards className without dropping defaults", () => {
    const { container } = render(
      <PageHeader title="t" className="custom-marker" />,
    );
    const header = container.querySelector("header") as HTMLElement;
    expect(header.className).toContain("custom-marker");
    expect(header.className).toContain("border-b");
  });

  it("uses the bottom-bordered layout token", () => {
    const { container } = render(<PageHeader title="t" />);
    const header = container.querySelector("header") as HTMLElement;
    expect(header.className).toContain("border-border");
    expect(header.className).toContain("pb-6");
  });
});
