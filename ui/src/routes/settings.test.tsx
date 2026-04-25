import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

// Stub each settings card so this test asserts composition only —
// the cards' own tests live next to the components.
vi.mock("@/features/settings", () => ({
  SettingsPage: () => {
    return <div data-testid="mock-settings-page" />;
  },
  ProfileViewPage: () => <div data-testid="mock-profile-view" />,
  ProfileEditorCard: () => <div data-testid="mock-profile-editor" />,
  EffectiveProfileCard: () => <div data-testid="mock-effective-profile" />,
  DriftCard: () => <div data-testid="mock-drift-card" />,
  EnvViewerCard: () => <div data-testid="mock-env-viewer" />,
  EnvVarsEditorCard: () => <div data-testid="mock-envvars-editor" />,
  DisplayPrefsCard: () => <div data-testid="mock-display-prefs" />,
  LogLevelCard: () => <div data-testid="mock-log-level" />,
}));

vi.mock("@/features/alerts/AlertRulesCard", () => ({
  AlertRulesCard: () => <div data-testid="mock-alert-rules-card" />,
}));

vi.mock("@/features/telemetry/TelemetryConsentCard", () => ({
  TelemetryConsentCard: () => <div data-testid="mock-telemetry-card" />,
}));

import { SettingsRoute, ProfileRoute } from "./$placeholder";

const SettingsPageComp = SettingsRoute.options.component as ComponentType;
const ProfilePageComp = ProfileRoute.options.component as ComponentType;

describe("settings route", () => {
  it("mounts the settings tabbed page at /settings", () => {
    renderWithProviders(<SettingsPageComp />);
    expect(screen.getByTestId("settings-page")).toBeInTheDocument();
    expect(
      (SettingsRoute.options as unknown as { path: string }).path,
    ).toBe("/settings");
  });

  it("mounts the ProfileViewPage at /profile", () => {
    renderWithProviders(<ProfilePageComp />);
    expect(screen.getByTestId("mock-profile-view")).toBeInTheDocument();
    expect(
      (ProfileRoute.options as unknown as { path: string }).path,
    ).toBe("/profile");
  });

  it("renders all six tab triggers including Alerts + Telemetry", () => {
    renderWithProviders(<SettingsPageComp />);
    expect(screen.getByTestId("settings-tab-profile")).toBeInTheDocument();
    expect(screen.getByTestId("settings-tab-environment")).toBeInTheDocument();
    expect(screen.getByTestId("settings-tab-display")).toBeInTheDocument();
    expect(screen.getByTestId("settings-tab-log-level")).toBeInTheDocument();
    expect(screen.getByTestId("settings-tab-alerts")).toBeInTheDocument();
    expect(screen.getByTestId("settings-tab-telemetry")).toBeInTheDocument();
  });

  it("mounts the AlertRulesCard when the Alerts tab is selected", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SettingsPageComp />);
    await user.click(screen.getByTestId("settings-tab-alerts"));
    expect(screen.getByTestId("mock-alert-rules-card")).toBeInTheDocument();
  });

  it("mounts the TelemetryConsentCard when the Telemetry tab is selected", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SettingsPageComp />);
    await user.click(screen.getByTestId("settings-tab-telemetry"));
    expect(screen.getByTestId("mock-telemetry-card")).toBeInTheDocument();
  });

  it("re-uses the existing settings cards in the original tabs", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SettingsPageComp />);
    // Profile tab is the default; the editor card mounts immediately.
    expect(screen.getByTestId("mock-profile-editor")).toBeInTheDocument();
    // Switching to Environment surfaces the env cards.
    await user.click(screen.getByTestId("settings-tab-environment"));
    const env = screen.getByTestId("mock-env-viewer");
    expect(within(env.parentElement!).getByTestId("mock-envvars-editor"))
      .toBeInTheDocument();
  });

  it("renders the EffectiveProfileCard above the editor on the Profile tab", () => {
    renderWithProviders(<SettingsPageComp />);
    const summary = screen.getByTestId("mock-effective-profile");
    const editor = screen.getByTestId("mock-profile-editor");
    expect(summary).toBeInTheDocument();
    expect(editor).toBeInTheDocument();
    // EffectiveProfileCard must render before the editor in DOM order.
    expect(
      summary.compareDocumentPosition(editor) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
