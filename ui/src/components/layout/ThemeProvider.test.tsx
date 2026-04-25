import { afterEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, useTheme } from "./ThemeProvider";

function ThemeProbe() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="theme">{theme ?? "—"}</span>
      <span data-testid="resolved">{resolvedTheme ?? "—"}</span>
      <button type="button" onClick={() => setTheme("dark")}>
        go-dark
      </button>
      <button type="button" onClick={() => setTheme("light")}>
        go-light
      </button>
    </div>
  );
}

describe("ThemeProvider", () => {
  afterEach(() => {
    // Clean side effects on document + storage between tests.
    document.documentElement.removeAttribute("data-theme");
    window.localStorage.removeItem("theme");
  });

  it("renders its children", () => {
    render(
      <ThemeProvider>
        <span>child</span>
      </ThemeProvider>,
    );
    expect(screen.getByText("child")).toBeInTheDocument();
  });

  it("useTheme exposes a theme + setTheme via the provider", () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("theme")).toBeInTheDocument();
    expect(screen.getByTestId("resolved")).toBeInTheDocument();
  });

  it("setTheme persists to localStorage under the 'theme' key", async () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );
    const dark = screen.getByText("go-dark");
    dark.click();
    await waitFor(() => {
      expect(window.localStorage.getItem("theme")).toBe("dark");
    });
  });

  it("setTheme mirrors the resolved theme onto <html data-theme>", async () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );
    screen.getByText("go-dark").click();
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("dark");
    });
    screen.getByText("go-light").click();
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("light");
    });
  });

  it("ThemeProvider is a function", () => {
    expect(typeof ThemeProvider).toBe("function");
  });
});
