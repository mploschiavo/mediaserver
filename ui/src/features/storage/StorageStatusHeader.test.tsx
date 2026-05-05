import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

import {
  StorageStatusHeader,
  formatSince,
  formatUntil,
  pickWorstMount,
  usageTone,
} from "./StorageStatusHeader";
import type { DiskGuardrailStatus } from "./hooks";

function makeStatus(over: Partial<DiskGuardrailStatus> = {}): DiskGuardrailStatus {
  return {
    state: "NORMAL",
    used_percent_by_mount: { config: 42.1, data: 65.8 },
    thresholds: { lockdown_percent: 75, release_percent: 60 },
    engaged_at: 0,
    engaged_by: "",
    trigger: null,
    auto_check_paused_until: null,
    paused_clients: [],
    last_failures: [],
    transitions: [],
    ...over,
  };
}

describe("pickWorstMount", () => {
  it("returns the highest used mount", () => {
    const w = pickWorstMount({ a: 12, b: 50, c: 30 });
    expect(w).toEqual({ label: "b", percent: 50 });
  });
  it("returns null on empty input", () => {
    expect(pickWorstMount({})).toBeNull();
  });
});

describe("usageTone", () => {
  it("classifies usage tones by threshold", () => {
    expect(usageTone(20)).toBe("success");
    expect(usageTone(50)).toBe("success");
    expect(usageTone(60)).toBe("warning");
    expect(usageTone(80)).toBe("critical");
  });
});

describe("formatSince / formatUntil", () => {
  it("formats freshly engaged as just now", () => {
    expect(formatSince(1000, 1010)).toBe("just now");
  });
  it("formats minutes / hours / days", () => {
    expect(formatSince(0, 60 * 5)).toBe("5 minutes ago");
    expect(formatSince(0, 3600 * 2)).toBe("2 hours ago");
    expect(formatSince(0, 86400 * 3)).toBe("3 days ago");
  });
  it("formats remaining TTL chip", () => {
    expect(formatUntil(60, 0)).toBe("1m left");
    expect(formatUntil(7200, 0)).toBe("2h left");
    expect(formatUntil(0, 100)).toBe("expired");
  });
});

describe("StorageStatusHeader", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Pin "now" so the engaged-since text is deterministic. Choose
    // 1746460000 + 12 minutes (720s) so a 12-minutes-ago state
    // engaged at 1746460000 produces the expected string.
    vi.setSystemTime(new Date(1746460000_000 + 720_000));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders NORMAL state with success tone and no engaged-by line", () => {
    renderWithProviders(<StorageStatusHeader status={makeStatus()} />);
    const badge = screen.getByTestId("storage-state-badge");
    expect(badge).toHaveAttribute("data-tone", "success");
    expect(badge.textContent).toMatch(/NORMAL/);
    expect(screen.queryByTestId("storage-engaged-since")).toBeNull();
  });

  it("renders MANUAL_LOCKDOWN with critical tone and engaged-by line", () => {
    renderWithProviders(
      <StorageStatusHeader
        status={makeStatus({
          state: "MANUAL_LOCKDOWN",
          engaged_at: 1746460000,
          engaged_by: "operator:matthew",
          paused_clients: ["qbittorrent", "sabnzbd"],
        })}
      />,
    );
    const badge = screen.getByTestId("storage-state-badge");
    expect(badge).toHaveAttribute("data-tone", "critical");
    expect(badge.textContent).toMatch(/MANUAL LOCKDOWN/);
    const since = screen.getByTestId("storage-engaged-since");
    expect(since.textContent).toMatch(/12 minutes ago/);
    expect(since.textContent).toMatch(/operator:matthew/);
    expect(
      screen.getByTestId("storage-paused-client-qbittorrent"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("storage-paused-client-sabnzbd"),
    ).toBeInTheDocument();
  });

  it("renders the paused-auto chip when TTL is in the future", () => {
    renderWithProviders(
      <StorageStatusHeader
        status={makeStatus({
          auto_check_paused_until:
            Math.floor(Date.now() / 1000) + 1800,
        })}
      />,
    );
    expect(screen.getByTestId("storage-pause-chip")).toHaveAttribute(
      "data-tone",
      "info",
    );
  });

  it("tones the usage bar critical when worst mount > 75%", () => {
    renderWithProviders(
      <StorageStatusHeader
        status={makeStatus({ used_percent_by_mount: { data: 80.5 } })}
      />,
    );
    expect(screen.getByTestId("storage-usage-bar")).toHaveAttribute(
      "data-tone",
      "critical",
    );
  });

  it("renders an explicit empty caption for paused clients", () => {
    renderWithProviders(<StorageStatusHeader status={makeStatus()} />);
    // Per `feedback_empty_state_visibility` we always render the row.
    expect(
      screen.getByTestId("storage-paused-clients-empty"),
    ).toBeInTheDocument();
  });
});
