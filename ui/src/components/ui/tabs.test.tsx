import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./tabs";

function renderTabs(defaultValue = "one") {
  return render(
    <Tabs defaultValue={defaultValue}>
      <TabsList>
        <TabsTrigger value="one">One</TabsTrigger>
        <TabsTrigger value="two">Two</TabsTrigger>
        <TabsTrigger value="three">Three</TabsTrigger>
      </TabsList>
      <TabsContent value="one">Panel one</TabsContent>
      <TabsContent value="two">Panel two</TabsContent>
      <TabsContent value="three">Panel three</TabsContent>
    </Tabs>,
  );
}

describe("Tabs", () => {
  it("renders the trigger list and the default panel", () => {
    renderTabs();
    expect(screen.getByRole("tab", { name: "One" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Two" })).toBeInTheDocument();
    expect(screen.getByText("Panel one")).toBeInTheDocument();
  });

  it("marks the default tab active", () => {
    renderTabs();
    const tab = screen.getByRole("tab", { name: "One" });
    expect(tab).toHaveAttribute("data-state", "active");
    expect(tab).toHaveAttribute("aria-selected", "true");
  });

  it("switches the active tab on click", async () => {
    renderTabs();
    await userEvent.click(screen.getByRole("tab", { name: "Two" }));
    expect(screen.getByRole("tab", { name: "Two" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByText("Panel two")).toBeInTheDocument();
  });

  it("supports arrow-key navigation between triggers", async () => {
    renderTabs();
    const first = screen.getByRole("tab", { name: "One" });
    first.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Two" })).toHaveFocus();
  });

  it("forwards className on TabsList", () => {
    render(
      <Tabs defaultValue="a">
        <TabsList className="extra-marker" data-testid="list">
          <TabsTrigger value="a">A</TabsTrigger>
        </TabsList>
        <TabsContent value="a">P</TabsContent>
      </Tabs>,
    );
    expect(screen.getByTestId("list").className).toContain("extra-marker");
  });

  it("forwards className on TabsTrigger", () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a" className="trigger-marker">
            A
          </TabsTrigger>
        </TabsList>
        <TabsContent value="a">P</TabsContent>
      </Tabs>,
    );
    expect(screen.getByRole("tab", { name: "A" }).className).toContain(
      "trigger-marker",
    );
  });

  it("forwards className on TabsContent", () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
        </TabsList>
        <TabsContent value="a" className="content-marker">
          P
        </TabsContent>
      </Tabs>,
    );
    expect(screen.getByText("P").className).toContain("content-marker");
  });

  it("forwards refs on each subcomponent", () => {
    const list = { current: null as HTMLDivElement | null };
    const trigger = { current: null as HTMLButtonElement | null };
    const content = { current: null as HTMLDivElement | null };
    render(
      <Tabs defaultValue="a">
        <TabsList ref={list}>
          <TabsTrigger ref={trigger} value="a">
            A
          </TabsTrigger>
        </TabsList>
        <TabsContent ref={content} value="a">
          P
        </TabsContent>
      </Tabs>,
    );
    expect(list.current).not.toBeNull();
    expect(trigger.current).not.toBeNull();
    expect(content.current).not.toBeNull();
  });
});
