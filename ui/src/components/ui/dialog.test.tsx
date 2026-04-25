import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "./dialog";

function renderDialog() {
  return render(
    <Dialog>
      <DialogTrigger>Open</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Confirm</DialogTitle>
          <DialogDescription>Are you sure?</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <button>OK</button>
        </DialogFooter>
      </DialogContent>
    </Dialog>,
  );
}

describe("Dialog", () => {
  it("renders the trigger and is closed by default", () => {
    renderDialog();
    expect(screen.getByText("Open")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("Confirm")).not.toBeInTheDocument();
  });

  it("opens the dialog when the trigger is clicked", async () => {
    renderDialog();
    await userEvent.click(screen.getByText("Open"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    // Radix Dialog 1.x sets aria-modal on its inner content wrapper
    // rather than the role="dialog" surface; assert the open data-state
    // instead, which is the Radix-stable signal.
    expect(dialog).toHaveAttribute("data-state", "open");
  });

  it("renders title + description inside the open dialog", async () => {
    renderDialog();
    await userEvent.click(screen.getByText("Open"));
    expect(await screen.findByText("Confirm")).toBeInTheDocument();
    expect(screen.getByText("Are you sure?")).toBeInTheDocument();
  });

  it("closes via the Escape key", async () => {
    renderDialog();
    await userEvent.click(screen.getByText("Open"));
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes via the built-in close button", async () => {
    renderDialog();
    await userEvent.click(screen.getByText("Open"));
    await screen.findByRole("dialog");
    // The X button has the sr-only label "Close".
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("forwards className on DialogContent", async () => {
    render(
      <Dialog defaultOpen>
        <DialogContent className="content-marker">
          <DialogTitle>T</DialogTitle>
          <DialogDescription>D</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog.className).toContain("content-marker");
  });

  it("DialogHeader and DialogFooter accept className", async () => {
    render(
      <Dialog defaultOpen>
        <DialogContent>
          <DialogHeader className="hdr-marker" data-testid="hdr">
            <DialogTitle>t</DialogTitle>
            <DialogDescription>d</DialogDescription>
          </DialogHeader>
          <DialogFooter className="ftr-marker" data-testid="ftr" />
        </DialogContent>
      </Dialog>,
    );
    expect((await screen.findByTestId("hdr")).className).toContain(
      "hdr-marker",
    );
    expect(screen.getByTestId("ftr").className).toContain("ftr-marker");
  });

  it("forwards ref on DialogContent", async () => {
    const ref = { current: null as HTMLDivElement | null };
    render(
      <Dialog defaultOpen>
        <DialogContent ref={ref}>
          <DialogTitle>T</DialogTitle>
          <DialogDescription>D</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    await screen.findByRole("dialog");
    expect(ref.current).not.toBeNull();
  });
});
