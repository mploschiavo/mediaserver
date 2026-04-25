import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const migrationState = vi.hoisted(() => ({
  data: undefined as
    | {
        ok?: boolean;
        blockers?: readonly string[];
        warnings?: readonly string[];
      }
    | undefined,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useValidateMigration: () => ({
    data: migrationState.data,
    isLoading: migrationState.isLoading,
    error: migrationState.error,
  }),
}));

import {
  MigrationCheckCard,
  migrationCheckHasContent,
} from "./MigrationCheckCard";

describe("MigrationCheckCard", () => {
  beforeEach(() => {
    migrationState.data = undefined;
    migrationState.isLoading = false;
    migrationState.error = null;
  });
  afterEach(() => {
    migrationState.data = undefined;
  });

  it("renders nothing on error", () => {
    migrationState.error = new Error("offline");
    const { container } = renderWithProviders(<MigrationCheckCard />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a skeleton while loading", () => {
    migrationState.isLoading = true;
    renderWithProviders(<MigrationCheckCard />);
    expect(
      screen.getByTestId("migration-check-card-loading"),
    ).toBeInTheDocument();
  });

  it("renders the green ok message when ok with no warnings", () => {
    migrationState.data = { ok: true, blockers: [], warnings: [] };
    renderWithProviders(<MigrationCheckCard />);
    expect(screen.getByTestId("migration-check-ok")).toBeInTheDocument();
  });

  it("renders blockers in the red panel", () => {
    migrationState.data = {
      ok: false,
      blockers: ["bad config", "missing volume"],
      warnings: [],
    };
    renderWithProviders(<MigrationCheckCard />);
    const blockers = screen.getByTestId("migration-check-blockers");
    expect(blockers).toBeInTheDocument();
    expect(blockers).toHaveTextContent(/bad config/);
    expect(blockers).toHaveTextContent(/missing volume/);
    expect(blockers).toHaveTextContent(/2 blockers/);
    expect(screen.queryByTestId("migration-check-ok")).not.toBeInTheDocument();
  });

  it("renders warnings in the amber panel", () => {
    migrationState.data = {
      ok: true,
      blockers: [],
      warnings: ["disk-tight"],
    };
    renderWithProviders(<MigrationCheckCard />);
    const warn = screen.getByTestId("migration-check-warnings");
    expect(warn).toHaveTextContent(/disk-tight/);
    expect(warn).toHaveTextContent(/1 warning/);
    // ok message is suppressed when warnings exist
    expect(screen.queryByTestId("migration-check-ok")).not.toBeInTheDocument();
  });

  it("migrationCheckHasContent reports false on undefined / empty", () => {
    expect(migrationCheckHasContent(undefined)).toBe(false);
    expect(
      migrationCheckHasContent({ ok: false, blockers: [], warnings: [] }),
    ).toBe(false);
  });

  it("migrationCheckHasContent reports true on any signal", () => {
    expect(migrationCheckHasContent({ ok: true })).toBe(true);
    expect(migrationCheckHasContent({ ok: false, blockers: ["x"] })).toBe(true);
    expect(migrationCheckHasContent({ ok: false, warnings: ["y"] })).toBe(true);
  });
});
