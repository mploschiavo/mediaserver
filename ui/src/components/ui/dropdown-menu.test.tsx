import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "./dropdown-menu";

function renderMenu() {
  return render(
    <DropdownMenu>
      <DropdownMenuTrigger>Open menu</DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel>Section</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem>Profile</DropdownMenuItem>
        <DropdownMenuItem>Settings</DropdownMenuItem>
        <DropdownMenuItem>
          Logout <DropdownMenuShortcut>⌘L</DropdownMenuShortcut>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>,
  );
}

describe("DropdownMenu", () => {
  it("is closed by default", () => {
    renderMenu();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("opens when the trigger is clicked", async () => {
    renderMenu();
    await userEvent.click(screen.getByText("Open menu"));
    expect(await screen.findByRole("menu")).toBeInTheDocument();
    expect(screen.getByText("Profile")).toBeInTheDocument();
  });

  it("closes when an item is selected", async () => {
    renderMenu();
    await userEvent.click(screen.getByText("Open menu"));
    await screen.findByRole("menu");
    await userEvent.click(screen.getByText("Profile"));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("supports arrow key navigation between items", async () => {
    renderMenu();
    await userEvent.click(screen.getByText("Open menu"));
    await screen.findByRole("menu");
    await userEvent.keyboard("{ArrowDown}");
    // First non-disabled menuitem should now be highlighted.
    const items = screen.getAllByRole("menuitem");
    expect(items[0]).toHaveAttribute("data-highlighted");
  });

  it("renders the shortcut label", async () => {
    renderMenu();
    await userEvent.click(screen.getByText("Open menu"));
    await screen.findByRole("menu");
    expect(screen.getByText("⌘L")).toBeInTheDocument();
  });

  it("forwards className on DropdownMenuItem", async () => {
    render(
      <DropdownMenu defaultOpen>
        <DropdownMenuTrigger>t</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem className="mi-marker">A</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );
    const item = await screen.findByRole("menuitem", { name: "A" });
    expect(item.className).toContain("mi-marker");
  });

  it("supports an inset item with extra left padding", async () => {
    render(
      <DropdownMenu defaultOpen>
        <DropdownMenuTrigger>t</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem inset>Inset item</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );
    const item = await screen.findByRole("menuitem", { name: "Inset item" });
    expect(item.className).toContain("pl-8");
  });

  it("forwards ref on DropdownMenuContent", async () => {
    const ref = { current: null as HTMLDivElement | null };
    render(
      <DropdownMenu defaultOpen>
        <DropdownMenuTrigger>t</DropdownMenuTrigger>
        <DropdownMenuContent ref={ref}>
          <DropdownMenuItem>A</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );
    await screen.findByRole("menu");
    expect(ref.current).not.toBeNull();
  });
});
