import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

interface MockedShape {
  steps?: readonly { id: string; label: string; status: string; detail: string }[];
  completed?: number;
  total?: number;
  progress_pct?: number;
  is_first_run?: boolean;
}

const onboardingState = vi.hoisted(() => ({
  data: undefined as MockedShape | undefined,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useOnboarding: () => ({
      data: onboardingState.data,
      isLoading: onboardingState.isLoading,
      error: onboardingState.error,
    }),
  };
});

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    to,
    children,
    ...rest
  }: {
    to: string;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

import {
  OnboardingChecklist,
  onboardingHasContent,
} from "./OnboardingChecklist";

describe("OnboardingChecklist", () => {
  beforeEach(() => {
    onboardingState.data = undefined;
    onboardingState.isLoading = false;
    onboardingState.error = null;
  });
  afterEach(() => {
    onboardingState.data = undefined;
  });

  it("renders nothing on error", () => {
    onboardingState.error = new Error("offline");
    const { container } = renderWithProviders(<OnboardingChecklist />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when total is zero", () => {
    onboardingState.data = {
      steps: [],
      completed: 0,
      total: 0,
      progress_pct: 0,
      is_first_run: false,
    };
    const { container } = renderWithProviders(<OnboardingChecklist />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a skeleton while loading", () => {
    onboardingState.isLoading = true;
    renderWithProviders(<OnboardingChecklist />);
    expect(
      screen.getByTestId("onboarding-checklist-loading"),
    ).toBeInTheDocument();
  });

  it("renders actionable steps prominently and hides done items behind a toggle", () => {
    onboardingState.data = {
      steps: [
        {
          id: "services_running",
          label: "Services running",
          status: "ok",
          detail: "12/14 healthy",
        },
        {
          id: "libraries",
          label: "Media libraries configured",
          status: "pending",
          detail: "No libraries",
        },
      ],
      completed: 1,
      total: 2,
      progress_pct: 50,
      is_first_run: true,
    };
    renderWithProviders(<OnboardingChecklist />);

    const actionable = screen.getByTestId("onboarding-checklist-actionable");
    expect(actionable).toHaveTextContent(/media libraries configured/i);

    expect(
      screen.queryByTestId("onboarding-checklist-done-list"),
    ).not.toBeInTheDocument();

    const toggle = screen.getByTestId("onboarding-checklist-done-toggle");
    expect(toggle).toHaveTextContent(/done for you/i);
    fireEvent.click(toggle);
    const done = screen.getByTestId("onboarding-checklist-done-list");
    expect(done).toHaveTextContent(/services running/i);
  });

  it("renders 'Resume setup' linking to the first actionable step's route", () => {
    onboardingState.data = {
      steps: [
        {
          id: "libraries",
          label: "Media libraries configured",
          status: "pending",
          detail: "No libraries",
        },
      ],
      completed: 0,
      total: 1,
      progress_pct: 0,
      is_first_run: true,
    };
    renderWithProviders(<OnboardingChecklist />);
    const resume = screen.getByTestId("onboarding-checklist-resume");
    const anchor =
      resume.tagName === "A" ? resume : resume.querySelector("a");
    expect(anchor).not.toBeNull();
    expect(anchor?.getAttribute("href")).toBe("/content");
  });

  it("celebrates the all-done state without a Resume CTA", () => {
    onboardingState.data = {
      steps: [
        {
          id: "services_running",
          label: "Services running",
          status: "ok",
          detail: "14/14 healthy",
        },
      ],
      completed: 1,
      total: 1,
      progress_pct: 100,
      is_first_run: false,
    };
    renderWithProviders(<OnboardingChecklist />);
    expect(
      screen.queryByTestId("onboarding-checklist-resume"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("onboarding-checklist")).toHaveTextContent(
      /your media stack is ready/i,
    );
  });

  it("onboardingHasContent returns true when there are steps or a positive total", () => {
    expect(onboardingHasContent(undefined)).toBe(false);
    expect(onboardingHasContent({ total: 0, steps: [] })).toBe(false);
    expect(onboardingHasContent({ total: 3, steps: [] })).toBe(true);
    expect(
      onboardingHasContent({
        total: 0,
        steps: [
          {
            id: "x",
            label: "x",
            status: "pending",
            detail: "",
          },
        ],
      }),
    ).toBe(true);
  });
});
