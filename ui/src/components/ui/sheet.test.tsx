import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "./sheet";

describe("Sheet", () => {
  it("renders only the trigger by default (closed)", () => {
    render(
      <Sheet>
        <SheetTrigger>Open</SheetTrigger>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Title</SheetTitle>
            <SheetDescription>Desc</SheetDescription>
          </SheetHeader>
        </SheetContent>
      </Sheet>,
    );
    expect(screen.getByText("Open")).toBeInTheDocument();
    expect(screen.queryByText("Title")).not.toBeInTheDocument();
  });

  it("opens when the trigger is activated", async () => {
    render(
      <Sheet>
        <SheetTrigger>Open</SheetTrigger>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>SheetTitle</SheetTitle>
            <SheetDescription>SheetDesc</SheetDescription>
          </SheetHeader>
        </SheetContent>
      </Sheet>,
    );
    await userEvent.click(screen.getByText("Open"));
    // Vaul renders the title in the portal once open.
    expect(await screen.findByText("SheetTitle")).toBeInTheDocument();
  });

  it("renders content via the open prop", async () => {
    render(
      <Sheet open>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>OpenTitle</SheetTitle>
            <SheetDescription>OpenDesc</SheetDescription>
          </SheetHeader>
          <SheetFooter>FooterText</SheetFooter>
        </SheetContent>
      </Sheet>,
    );
    expect(await screen.findByText("OpenTitle")).toBeInTheDocument();
    expect(screen.getByText("FooterText")).toBeInTheDocument();
  });

  it("forwards className on SheetContent", async () => {
    render(
      <Sheet open>
        <SheetContent className="content-marker" data-testid="sc">
          <SheetTitle>T</SheetTitle>
          <SheetDescription>D</SheetDescription>
        </SheetContent>
      </Sheet>,
    );
    const el = await screen.findByTestId("sc");
    expect(el.className).toContain("content-marker");
  });

  it("SheetHeader and SheetFooter accept className", () => {
    render(
      <Sheet open>
        <SheetContent>
          <SheetHeader className="h-mk" data-testid="h">
            <SheetTitle>t</SheetTitle>
            <SheetDescription>d</SheetDescription>
          </SheetHeader>
          <SheetFooter className="f-mk" data-testid="f" />
        </SheetContent>
      </Sheet>,
    );
    expect(screen.getByTestId("h").className).toContain("h-mk");
    expect(screen.getByTestId("f").className).toContain("f-mk");
  });

  it("SheetTitle uses semibold + tracking-tight tokens", async () => {
    render(
      <Sheet open>
        <SheetContent>
          <SheetTitle data-testid="t">x</SheetTitle>
          <SheetDescription>y</SheetDescription>
        </SheetContent>
      </Sheet>,
    );
    const t = await screen.findByTestId("t");
    expect(t.className).toContain("font-semibold");
    expect(t.className).toContain("tracking-tight");
  });
});
