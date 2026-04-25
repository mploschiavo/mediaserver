import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const onboardingState = vi.hoisted(() => ({
  data: undefined as
    | {
        step?: string;
        completed?: readonly unknown[];
        pending?: readonly unknown[];
      }
    | undefined,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useOnboarding: () => ({
    data: onboardingState.data,
    isLoading: onboardingState.isLoading,
    error: onboardingState.error,
  }),
}));

vi.mock("@tanstack/react-router", () => ({
  // Anchor stand-in for the home-route Link.
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

  it("renders nothing when there is no work and nothing completed", () => {
    onboardingState.data = { completed: [], pending: [] };
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

  it("renders the completed + pending lists", () => {
    onboardingState.data = {
      completed: ["welcome", { label: "set admin" }],
      pending: [
        { id: "indexers", label: "Indexers", route: "/indexers" },
        "Quality profiles",
      ],
    };
    renderWithProviders(<OnboardingChecklist />);
    const completed = screen.getByTestId("onboarding-checklist-completed");
    expect(completed).toHaveTextContent(/welcome/);
    expect(completed).toHaveTextContent(/set admin/);
    const pending = screen.getByTestId("onboarding-checklist-pending");
    expect(pending).toHaveTextContent(/Indexers/);
    expect(pending).toHaveTextContent(/Quality profiles/);
  });

  it("renders 'Resume setup' linking to the first pending route", () => {
    onboardingState.data = {
      completed: [],
      pending: [
        "no-route step",
        { label: "Indexers", route: "/indexers" },
        { label: "later", route: "/later" },
      ],
    };
    renderWithProviders(<OnboardingChecklist />);
    const resume = screen.getByTestId("onboarding-checklist-resume");
    // Button rendered asChild, so the anchor child holds the href.
    const anchor = resume.tagName === "A" ? resume : resume.querySelector("a");
    expect(anchor).not.toBeNull();
    expect(anchor?.getAttribute("href")).toBe("/indexers");
  });

  it("hides 'Resume setup' when there are no pending steps", () => {
    onboardingState.data = {
      completed: ["welcome"],
      pending: [],
    };
    renderWithProviders(<OnboardingChecklist />);
    expect(
      screen.queryByTestId("onboarding-checklist-resume"),
    ).not.toBeInTheDocument();
  });

  it("onboardingHasContent matches the completed/pending guards", () => {
    expect(onboardingHasContent(undefined)).toBe(false);
    expect(onboardingHasContent({ completed: [], pending: [] })).toBe(false);
    expect(onboardingHasContent({ completed: ["x"], pending: [] })).toBe(true);
    expect(onboardingHasContent({ completed: [], pending: ["x"] })).toBe(true);
  });
});
