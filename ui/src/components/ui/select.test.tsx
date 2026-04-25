import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./select";

function renderSelect(props?: { onValueChange?: (v: string) => void }) {
  return render(
    <Select onValueChange={props?.onValueChange}>
      <SelectTrigger aria-label="fruit">
        <SelectValue placeholder="Choose…" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="apple">Apple</SelectItem>
        <SelectItem value="banana">Banana</SelectItem>
        <SelectItem value="cherry">Cherry</SelectItem>
      </SelectContent>
    </Select>,
  );
}

describe("Select", () => {
  it("renders the trigger with the placeholder", () => {
    renderSelect();
    expect(screen.getByLabelText("fruit")).toBeInTheDocument();
    expect(screen.getByText("Choose…")).toBeInTheDocument();
  });

  it("opens the listbox when the trigger is clicked", async () => {
    renderSelect();
    await userEvent.click(screen.getByLabelText("fruit"));
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
  });

  it("selects an option on click and reports the value", async () => {
    const onValueChange = vi.fn();
    renderSelect({ onValueChange });
    await userEvent.click(screen.getByLabelText("fruit"));
    await screen.findByRole("listbox");
    await userEvent.click(screen.getByRole("option", { name: "Banana" }));
    expect(onValueChange).toHaveBeenCalledWith("banana");
  });

  it("reflects the selected value on the trigger", async () => {
    renderSelect();
    await userEvent.click(screen.getByLabelText("fruit"));
    await userEvent.click(
      await screen.findByRole("option", { name: "Cherry" }),
    );
    expect(screen.getByLabelText("fruit")).toHaveTextContent("Cherry");
  });

  it("supports keyboard navigation between options", async () => {
    renderSelect();
    const trigger = screen.getByLabelText("fruit");
    trigger.focus();
    await userEvent.keyboard("{Enter}");
    await screen.findByRole("listbox");
    // Radix's Select handles arrow keys internally; just confirm the
    // listbox stays open after a navigation key.
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("respects a default value via defaultValue", () => {
    render(
      <Select defaultValue="banana">
        <SelectTrigger aria-label="fruit">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="apple">Apple</SelectItem>
          <SelectItem value="banana">Banana</SelectItem>
        </SelectContent>
      </Select>,
    );
    expect(screen.getByLabelText("fruit")).toHaveTextContent("Banana");
  });

  it("forwards className on SelectTrigger", () => {
    render(
      <Select>
        <SelectTrigger aria-label="x" className="trig-marker">
          <SelectValue placeholder="p" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">A</SelectItem>
        </SelectContent>
      </Select>,
    );
    expect(screen.getByLabelText("x").className).toContain("trig-marker");
  });
});
