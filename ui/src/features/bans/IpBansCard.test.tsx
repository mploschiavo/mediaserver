import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const ipBansState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const addMutate = vi.hoisted(() => vi.fn());
const removeMutate = vi.hoisted(() => vi.fn());
const addState = vi.hoisted(() => ({ isPending: false }));
const removeState = vi.hoisted(() => ({ isPending: false }));

const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useIpBans: () => ipBansState,
  useAddIpBan: () => ({ mutate: addMutate, ...addState }),
  useRemoveIpBan: () => ({ mutate: removeMutate, ...removeState }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { IpBansCard, isValidCidr } from "./IpBansCard";

describe("isValidCidr", () => {
  it("accepts bare IPv4 addresses", () => {
    expect(isValidCidr("192.168.1.1")).toBe(true);
  });
  it("accepts IPv4 with prefix", () => {
    expect(isValidCidr("192.168.0.0/24")).toBe(true);
    expect(isValidCidr("10.0.0.0/8")).toBe(true);
  });
  it("rejects out-of-range octets", () => {
    expect(isValidCidr("999.0.0.1")).toBe(false);
  });
  it("rejects out-of-range prefixes", () => {
    expect(isValidCidr("10.0.0.0/64")).toBe(false);
  });
  it("rejects bare gibberish", () => {
    expect(isValidCidr("not-an-ip")).toBe(false);
  });
  it("accepts IPv6-shaped strings", () => {
    expect(isValidCidr("2001:db8::1")).toBe(true);
    expect(isValidCidr("2001:db8::/32")).toBe(true);
  });
  it("rejects empty input", () => {
    expect(isValidCidr("")).toBe(false);
    expect(isValidCidr("   ")).toBe(false);
  });
});

describe("IpBansCard", () => {
  beforeEach(() => {
    ipBansState.data = undefined;
    ipBansState.isLoading = false;
    ipBansState.error = null;
    addState.isPending = false;
    removeState.isPending = false;
    addMutate.mockReset();
    removeMutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the loading skeleton while fetching", () => {
    ipBansState.isLoading = true;
    renderWithProviders(<IpBansCard />);
    expect(screen.getByTestId("ip-bans-loading")).toBeInTheDocument();
  });

  it("renders the empty state when no bans exist", () => {
    ipBansState.data = [];
    renderWithProviders(<IpBansCard />);
    expect(screen.getByText(/No IP bans/i)).toBeInTheDocument();
  });

  it("renders one row per ban", () => {
    ipBansState.data = [
      {
        cidr: "203.0.113.0/24",
        reason: "scraper",
        banned_at: "2026-04-01T12:00:00Z",
      },
      { cidr: "198.51.100.7", reason: "abuse" },
    ];
    renderWithProviders(<IpBansCard />);
    expect(
      screen.getByTestId("ip-ban-row-203.0.113.0/24"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("ip-ban-row-198.51.100.7"),
    ).toBeInTheDocument();
    expect(screen.getByText("203.0.113.0/24")).toBeInTheDocument();
    expect(screen.getByText("scraper")).toBeInTheDocument();
  });

  it("filters bans via the DataTable reason filter", async () => {
    ipBansState.data = [
      {
        cidr: "203.0.113.0/24",
        reason: "scraper",
        banned_at: "2026-04-01T12:00:00Z",
      },
      { cidr: "198.51.100.7", reason: "abuse" },
    ];
    renderWithProviders(<IpBansCard />);
    expect(
      screen.getByTestId("ip-ban-row-203.0.113.0/24"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("ip-ban-row-198.51.100.7"),
    ).toBeInTheDocument();
    await userEvent.type(screen.getByTestId("ip-ban-filter-reason"), "abuse");
    expect(screen.queryByTestId("ip-ban-row-203.0.113.0/24")).toBeNull();
    expect(
      screen.getByTestId("ip-ban-row-198.51.100.7"),
    ).toBeInTheDocument();
  });

  it("opens the dialog, validates a bad CIDR inline, and does not call mutation", async () => {
    ipBansState.data = [];
    renderWithProviders(<IpBansCard />);
    await userEvent.click(screen.getByTestId("ip-ban-add-trigger"));
    await waitFor(() =>
      expect(screen.getByTestId("ip-ban-dialog")).toBeInTheDocument(),
    );
    await userEvent.type(
      screen.getByTestId("ip-ban-cidr-input"),
      "not-an-ip",
    );
    await userEvent.click(screen.getByTestId("ip-ban-submit"));
    expect(screen.getByTestId("ip-ban-cidr-error")).toBeInTheDocument();
    expect(addMutate).not.toHaveBeenCalled();
  });

  it("submits a valid CIDR and calls the add mutation", async () => {
    ipBansState.data = [];
    renderWithProviders(<IpBansCard />);
    await userEvent.click(screen.getByTestId("ip-ban-add-trigger"));
    await userEvent.type(
      screen.getByTestId("ip-ban-cidr-input"),
      "10.0.0.0/8",
    );
    await userEvent.type(
      screen.getByTestId("ip-ban-reason-input"),
      "abuse",
    );
    await userEvent.click(screen.getByTestId("ip-ban-submit"));
    expect(addMutate).toHaveBeenCalledTimes(1);
    expect(addMutate.mock.calls[0]?.[0]).toMatchObject({
      cidr: "10.0.0.0/8",
      reason: "abuse",
    });
  });

  it("disables the submit button until a CIDR is typed", async () => {
    ipBansState.data = [];
    renderWithProviders(<IpBansCard />);
    await userEvent.click(screen.getByTestId("ip-ban-add-trigger"));
    const submit = await screen.findByTestId("ip-ban-submit");
    expect(submit).toBeDisabled();
    await userEvent.type(
      screen.getByTestId("ip-ban-cidr-input"),
      "10.0.0.0/8",
    );
    expect(submit).not.toBeDisabled();
  });

  it("calls the lift-ban mutation after the user confirms", async () => {
    ipBansState.data = [{ cidr: "10.0.0.0/8", reason: "x" }];
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithProviders(<IpBansCard />);
    await userEvent.click(screen.getByTestId("ip-ban-lift-10.0.0.0/8"));
    expect(confirmSpy).toHaveBeenCalled();
    expect(removeMutate).toHaveBeenCalledTimes(1);
    expect(removeMutate.mock.calls[0]?.[0]).toMatchObject({
      cidr: "10.0.0.0/8",
    });
  });

  it("does not lift the ban when the confirm dialog is cancelled", async () => {
    ipBansState.data = [{ cidr: "10.0.0.0/8", reason: "x" }];
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithProviders(<IpBansCard />);
    await userEvent.click(screen.getByTestId("ip-ban-lift-10.0.0.0/8"));
    expect(removeMutate).not.toHaveBeenCalled();
  });

  it("renders the error banner when the query fails", () => {
    ipBansState.error = new Error("kaboom");
    renderWithProviders(<IpBansCard />);
    expect(screen.getByTestId("ip-bans-error")).toHaveTextContent("kaboom");
  });
});
