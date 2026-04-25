import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
  getBaseUrl: () => "",
}));

import { GuardrailDomainTab } from "./GuardrailDomainTab";
import type { Guardrail } from "./hooks";

const RULES: Guardrail[] = [
  {
    id: "storage:per_mount_threshold",
    domain: "storage",
    description: "x",
    threshold: { max_percent: 85 },
  },
  {
    id: "auth:failed_login_spike",
    domain: "auth",
    description: "y",
    threshold: { alert_count: 5 },
  },
];

describe("GuardrailDomainTab", () => {
  it("filters rules by domain", () => {
    renderWithProviders(
      <GuardrailDomainTab domain="storage" rules={RULES} />,
    );
    expect(
      screen.getByTestId("guardrail-row-storage:per_mount_threshold"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("guardrail-row-auth:failed_login_spike"),
    ).toBeNull();
  });

  it("renders empty state when no rules match the domain", () => {
    renderWithProviders(
      <GuardrailDomainTab domain="cost" rules={RULES} />,
    );
    expect(screen.getByTestId("guardrail-domain-empty-cost")).toBeInTheDocument();
  });
});
