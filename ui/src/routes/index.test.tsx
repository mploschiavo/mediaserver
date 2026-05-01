import type { ComponentType } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

interface OnboardingDataShape {
  steps?: readonly { status?: string }[];
  total?: number;
  progress_pct?: number;
  is_first_run?: boolean;
}

const onboardingState = vi.hoisted(() => ({
  data: undefined as OnboardingDataShape | undefined,
  isLoading: false,
  error: null as Error | null,
}));
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
const navigateMock = vi.hoisted(() => vi.fn());

vi.mock("@/features/onboarding/hooks", () => ({
  useOnboarding: () => onboardingState,
}));
vi.mock("@/features/onboarding/OnboardingChecklist", () => ({
  OnboardingChecklist: () => <div data-testid="onboarding-stub" />,
  onboardingHasContent: (data: OnboardingDataShape | undefined): boolean => {
    if (!data) return false;
    if (typeof data.total === "number" && data.total > 0) return true;
    if (Array.isArray(data.steps) && data.steps.length > 0) return true;
    return false;
  },
}));
vi.mock("@/features/onboarding/QuickStartCards", () => ({
  QuickStartCards: () => <div data-testid="quick-start-stub" />,
}));
vi.mock("@/features/stack-lifecycle/hooks", () => ({
  useValidateMigration: () => migrationState,
}));
vi.mock("@/features/stack-lifecycle/MigrationCheckCard", () => ({
  MigrationCheckCard: () => <div data-testid="migration-stub" />,
  migrationCheckHasContent: (
    data:
      | {
          ok?: boolean;
          blockers?: readonly string[];
          warnings?: readonly string[];
        }
      | undefined,
  ): boolean => {
    if (!data) return false;
    const b = Array.isArray(data.blockers) ? data.blockers.length : 0;
    const w = Array.isArray(data.warnings) ? data.warnings.length : 0;
    return b + w > 0 || data.ok === true;
  },
}));

vi.mock("@tanstack/react-router", async () => {
  const actual =
    await vi.importActual<typeof import("@tanstack/react-router")>(
      "@tanstack/react-router",
    );
  return {
    ...actual,
    Navigate: (props: { to: string }) => {
      navigateMock(props.to);
      return <div data-testid="navigate-stub" data-to={props.to} />;
    },
  };
});

import { Route as IndexRoute } from "./index";

const HomePage = IndexRoute.options.component as ComponentType;

describe("home route (/)", () => {
  beforeEach(() => {
    onboardingState.data = undefined;
    onboardingState.isLoading = false;
    onboardingState.error = null;
    migrationState.data = undefined;
    migrationState.isLoading = false;
    migrationState.error = null;
    navigateMock.mockReset();
  });
  afterEach(() => {
    onboardingState.data = undefined;
    migrationState.data = undefined;
  });

  it("registers at /", () => {
    expect((IndexRoute.options as unknown as { path: string }).path).toBe("/");
  });

  it("redirects to /ops when neither card has content", () => {
    onboardingState.data = {
      steps: [],
      total: 0,
      progress_pct: 0,
      is_first_run: false,
    };
    migrationState.data = { ok: false, blockers: [], warnings: [] };
    renderWithProviders(<HomePage />);
    expect(navigateMock).toHaveBeenCalledWith("/ops");
  });

  it("renders the onboarding stub when there is auto-tracked content", () => {
    onboardingState.data = {
      steps: [{ status: "pending" }],
      total: 1,
      progress_pct: 30,
      is_first_run: true,
    };
    migrationState.data = { ok: false };
    renderWithProviders(<HomePage />);
    expect(screen.getByTestId("home-page")).toBeInTheDocument();
    expect(screen.getByTestId("onboarding-stub")).toBeInTheDocument();
    expect(screen.queryByTestId("migration-stub")).not.toBeInTheDocument();
  });

  it("hides QuickStartCards while progress is below the threshold", () => {
    onboardingState.data = {
      steps: [{ status: "pending" }],
      total: 6,
      progress_pct: 50,
      is_first_run: true,
    };
    migrationState.data = { ok: false };
    renderWithProviders(<HomePage />);
    expect(screen.getByTestId("onboarding-stub")).toBeInTheDocument();
    expect(screen.queryByTestId("quick-start-stub")).not.toBeInTheDocument();
  });

  it("reveals QuickStartCards once progress crosses the threshold", () => {
    onboardingState.data = {
      steps: [{ status: "ok" }],
      total: 6,
      progress_pct: 90,
      is_first_run: false,
    };
    migrationState.data = { ok: false };
    renderWithProviders(<HomePage />);
    expect(screen.getByTestId("quick-start-stub")).toBeInTheDocument();
  });

  it("renders the migration stub when blockers/warnings/ok are present", () => {
    onboardingState.data = {
      steps: [],
      total: 0,
      progress_pct: 0,
      is_first_run: false,
    };
    migrationState.data = { ok: true };
    renderWithProviders(<HomePage />);
    expect(screen.getByTestId("home-page")).toBeInTheDocument();
    expect(screen.getByTestId("migration-stub")).toBeInTheDocument();
    expect(screen.queryByTestId("onboarding-stub")).not.toBeInTheDocument();
  });

  it("renders nothing while either probe is loading", () => {
    onboardingState.isLoading = true;
    const { container } = renderWithProviders(<HomePage />);
    expect(container.firstChild).toBeNull();
  });
});
