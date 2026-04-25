import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const headState = vi.hoisted(() => ({
  data: undefined as { height: number; hash: string } | undefined,
  isLoading: false,
  error: null as Error | null,
}));

const verifyMutate = vi.hoisted(() => vi.fn());
const verifyState = vi.hoisted(() => ({ isPending: false }));

vi.mock("./hooks", () => ({
  useAuditLogHead: () => headState,
  useAuditLogVerify: () => ({
    mutate: verifyMutate,
    isPending: verifyState.isPending,
  }),
}));

import { IntegrityBanner, abbreviateHash } from "./IntegrityBanner";

describe("abbreviateHash", () => {
  it("returns '—' for empty input", () => {
    expect(abbreviateHash("")).toBe("—");
  });

  it("passes through short strings unchanged", () => {
    expect(abbreviateHash("abc123")).toBe("abc123");
  });

  it("compresses long hex digests with an ellipsis", () => {
    const hash = "a".repeat(60) + "z".repeat(4);
    const result = abbreviateHash(hash);
    expect(result.startsWith("aaaaaaaa")).toBe(true);
    expect(result.endsWith("zzzz")).toBe(true);
    expect(result).toContain("…");
  });
});

describe("IntegrityBanner", () => {
  beforeEach(() => {
    headState.data = undefined;
    headState.isLoading = false;
    headState.error = null;
    verifyState.isPending = false;
    verifyMutate.mockReset();
  });
  afterEach(() => {
    verifyMutate.mockReset();
  });

  it("renders the head loading skeleton while head is loading", () => {
    headState.isLoading = true;
    renderWithProviders(<IntegrityBanner />);
    expect(screen.getByTestId("integrity-head-loading")).toBeInTheDocument();
  });

  it("renders the head error inline when the head query fails", () => {
    headState.error = new Error("admin only");
    renderWithProviders(<IntegrityBanner />);
    expect(screen.getByTestId("integrity-head-error")).toHaveTextContent(
      "admin only",
    );
  });

  it("renders the abbreviated head hash and entry count", () => {
    headState.data = {
      height: 42,
      hash: "deadbeef".repeat(8),
    };
    renderWithProviders(<IntegrityBanner />);
    expect(screen.getByTestId("integrity-head-hash")).toHaveTextContent(
      /deadbeef…/,
    );
    expect(screen.getByTestId("integrity-head-height")).toHaveTextContent(
      /42 entries/,
    );
  });

  it("renders the singular 'entry' label when the chain has exactly one row", () => {
    headState.data = { height: 1, hash: "abc" };
    renderWithProviders(<IntegrityBanner />);
    expect(screen.getByTestId("integrity-head-height")).toHaveTextContent(
      /^1 entry$/,
    );
  });

  it("calls the verify mutation when the button is clicked", () => {
    headState.data = { height: 0, hash: "" };
    renderWithProviders(<IntegrityBanner />);
    fireEvent.click(screen.getByTestId("integrity-verify"));
    expect(verifyMutate).toHaveBeenCalledTimes(1);
  });

  it("renders the green tick when verify resolves with ok=true", async () => {
    verifyMutate.mockImplementation(
      (
        _v: void,
        opts: {
          onSuccess: (out: { ok: boolean; detail?: string }) => void;
        },
      ) => {
        opts.onSuccess({ ok: true, detail: "" });
      },
    );
    headState.data = { height: 7, hash: "abcd1234" };
    renderWithProviders(<IntegrityBanner />);
    fireEvent.click(screen.getByTestId("integrity-verify"));
    await waitFor(() => {
      expect(screen.getByTestId("integrity-result-ok")).toBeInTheDocument();
    });
    expect(screen.getByTestId("integrity-result-ok")).toHaveTextContent(
      /Chain intact \(7 entries\)/,
    );
  });

  it("renders the red banner with parsed entry index when verify reports a break", async () => {
    verifyMutate.mockImplementation(
      (
        _v: void,
        opts: {
          onSuccess: (out: { ok: boolean; detail?: string }) => void;
        },
      ) => {
        opts.onSuccess({ ok: false, detail: "entry 12: hash mismatch" });
      },
    );
    headState.data = { height: 13, hash: "" };
    renderWithProviders(<IntegrityBanner />);
    fireEvent.click(screen.getByTestId("integrity-verify"));
    await waitFor(() => {
      expect(
        screen.getByTestId("integrity-result-broken"),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("integrity-result-broken")).toHaveTextContent(
      /Chain broken at entry 12/,
    );
    expect(screen.getByTestId("integrity-result-detail")).toHaveTextContent(
      "entry 12: hash mismatch",
    );
  });

  it("falls back to a generic break message when the detail has no entry prefix", async () => {
    verifyMutate.mockImplementation(
      (
        _v: void,
        opts: {
          onSuccess: (out: { ok: boolean; detail?: string }) => void;
        },
      ) => {
        opts.onSuccess({ ok: false, detail: "tamper detected" });
      },
    );
    headState.data = { height: 1, hash: "" };
    renderWithProviders(<IntegrityBanner />);
    fireEvent.click(screen.getByTestId("integrity-verify"));
    await waitFor(() => {
      expect(
        screen.getByTestId("integrity-result-broken"),
      ).toHaveTextContent(/Chain broken/);
    });
    expect(
      screen.getByTestId("integrity-result-broken"),
    ).not.toHaveTextContent(/at entry/);
  });

  it("renders the broken banner with the error message when verify rejects", async () => {
    verifyMutate.mockImplementation(
      (_v: void, opts: { onError: (e: Error) => void }) => {
        opts.onError(new Error("rate limited"));
      },
    );
    headState.data = { height: 0, hash: "" };
    renderWithProviders(<IntegrityBanner />);
    fireEvent.click(screen.getByTestId("integrity-verify"));
    await waitFor(() => {
      expect(
        screen.getByTestId("integrity-result-broken"),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("integrity-result-detail")).toHaveTextContent(
      "rate limited",
    );
  });
});
