import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "./command";

function renderCommand() {
  return render(
    <Command>
      <CommandInput placeholder="Search…" />
      <CommandList>
        <CommandEmpty>No results.</CommandEmpty>
        <CommandGroup heading="Fruit">
          <CommandItem value="apple">
            Apple <CommandShortcut>A</CommandShortcut>
          </CommandItem>
          <CommandItem value="banana">Banana</CommandItem>
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Veg">
          <CommandItem value="carrot">Carrot</CommandItem>
        </CommandGroup>
      </CommandList>
    </Command>,
  );
}

describe("Command", () => {
  it("renders the input and items", () => {
    renderCommand();
    expect(screen.getByPlaceholderText("Search…")).toBeInTheDocument();
    expect(screen.getByText("Apple")).toBeInTheDocument();
    expect(screen.getByText("Banana")).toBeInTheDocument();
    expect(screen.getByText("Carrot")).toBeInTheDocument();
  });

  it("renders group headings", () => {
    renderCommand();
    expect(screen.getByText("Fruit")).toBeInTheDocument();
    expect(screen.getByText("Veg")).toBeInTheDocument();
  });

  it("filters items by typing", async () => {
    renderCommand();
    await userEvent.type(screen.getByPlaceholderText("Search…"), "carr");
    await waitFor(() => {
      expect(screen.queryByText("Apple")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Carrot")).toBeInTheDocument();
  });

  it("shows the empty state when nothing matches", async () => {
    renderCommand();
    await userEvent.type(
      screen.getByPlaceholderText("Search…"),
      "zzzznomatch",
    );
    await waitFor(() => {
      expect(screen.getByText("No results.")).toBeInTheDocument();
    });
  });

  it("renders a CommandShortcut tag", () => {
    renderCommand();
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("forwards className on Command root", () => {
    render(
      <Command className="cmd-marker" data-testid="cmd">
        <CommandList>
          <CommandItem>x</CommandItem>
        </CommandList>
      </Command>,
    );
    expect(screen.getByTestId("cmd").className).toContain("cmd-marker");
  });

  it("CommandInput accepts className", () => {
    render(
      <Command>
        <CommandInput placeholder="p" className="inp-marker" />
      </Command>,
    );
    expect(screen.getByPlaceholderText("p").className).toContain("inp-marker");
  });

  it("supports arrow-key item highlighting", async () => {
    renderCommand();
    const input = screen.getByPlaceholderText("Search…");
    input.focus();
    await userEvent.keyboard("{ArrowDown}");
    // cmdk marks the first focusable item with data-selected on arrow nav.
    await waitFor(() => {
      const items = screen.getAllByRole("option");
      const selected = items.find(
        (el) => el.getAttribute("data-selected") === "true",
      );
      expect(selected).toBeDefined();
    });
  });
});
