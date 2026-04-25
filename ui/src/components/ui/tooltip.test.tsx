import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "./tooltip";

describe("Tooltip", () => {
  it("renders the trigger without showing content", () => {
    render(
      <TooltipProvider delayDuration={0}>
        <Tooltip>
          <TooltipTrigger>Hover me</TooltipTrigger>
          <TooltipContent>Helpful</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );
    expect(screen.getByText("Hover me")).toBeInTheDocument();
    expect(screen.queryByText("Helpful")).not.toBeInTheDocument();
  });

  it("shows the tooltip after hover", async () => {
    render(
      <TooltipProvider delayDuration={0} skipDelayDuration={0}>
        <Tooltip>
          <TooltipTrigger>Hover me</TooltipTrigger>
          <TooltipContent>Helpful</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );
    await userEvent.hover(screen.getByText("Hover me"));
    await waitFor(() => {
      expect(screen.getAllByText("Helpful").length).toBeGreaterThan(0);
    });
  });

  it("opens on focus of the trigger", async () => {
    render(
      <TooltipProvider delayDuration={0} skipDelayDuration={0}>
        <Tooltip>
          <TooltipTrigger>Hover me</TooltipTrigger>
          <TooltipContent>Focus shown</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );
    const trigger = screen.getByText("Hover me");
    trigger.focus();
    await waitFor(() => {
      expect(screen.getAllByText("Focus shown").length).toBeGreaterThan(0);
    });
  });

  it("respects an explicit open prop", () => {
    render(
      <TooltipProvider>
        <Tooltip open>
          <TooltipTrigger>Trigger</TooltipTrigger>
          <TooltipContent>Always</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );
    expect(screen.getAllByText("Always").length).toBeGreaterThan(0);
  });

  it("forwards className on the content", () => {
    render(
      <TooltipProvider>
        <Tooltip open>
          <TooltipTrigger>T</TooltipTrigger>
          <TooltipContent className="cls-marker">Body</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );
    const matches = screen.getAllByText("Body");
    const found = matches.find((el) => el.className.includes("cls-marker"));
    expect(found).toBeDefined();
  });
});
