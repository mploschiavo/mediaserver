import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const countriesState = vi.hoisted(() => ({
  data: undefined as { countries?: readonly unknown[] } | undefined,
  isLoading: false,
  error: null as Error | null,
}));

const sourcesState = vi.hoisted(() => ({
  data: undefined as
    | {
        tuners?: readonly { url: string; name: string }[];
        guides?: readonly { url: string; name: string }[];
      }
    | undefined,
  isLoading: false,
  error: null as Error | null,
}));

const saveMutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useIptvCountries: () => countriesState,
  useLivetvSources: () => sourcesState,
  useSaveLivetvSources: () => ({ mutate: saveMutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { IptvCountriesCard } from "./IptvCountriesCard";

beforeEach(() => {
  countriesState.data = {
    countries: [
      { code: "US", name: "United States", m3u_url: "https://x/us.m3u" },
      { code: "DE", name: "Germany", m3u_url: "https://x/de.m3u" },
      { code: "FR", name: "France" },
    ],
  };
  countriesState.isLoading = false;
  countriesState.error = null;
  sourcesState.data = { tuners: [], guides: [] };
  sourcesState.isLoading = false;
  sourcesState.error = null;
  saveMutate.mockReset();
});

describe("IptvCountriesCard", () => {
  it("renders a loading skeleton while the query resolves", () => {
    countriesState.isLoading = true;
    countriesState.data = undefined;
    renderWithProviders(<IptvCountriesCard />);
    expect(screen.getByTestId("iptv-countries-loading")).toBeInTheDocument();
  });

  it("renders an error message when the query fails", () => {
    countriesState.error = new Error("countries gone");
    countriesState.data = undefined;
    renderWithProviders(<IptvCountriesCard />);
    expect(screen.getByTestId("iptv-countries-error")).toHaveTextContent(
      "countries gone",
    );
  });

  it("renders the empty state when no countries are returned", () => {
    countriesState.data = { countries: [] };
    renderWithProviders(<IptvCountriesCard />);
    expect(screen.getByText(/No countries available/i)).toBeInTheDocument();
  });

  it("renders the country dropdown options from the query data", () => {
    renderWithProviders(<IptvCountriesCard />);
    const select = screen.getByTestId("iptv-country") as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    const optionLabels = Array.from(select.options).map((o) => o.textContent);
    expect(optionLabels.join(" ")).toMatch(/United States/);
    expect(optionLabels.join(" ")).toMatch(/Germany/);
  });

  it("filters the dropdown by the search query", async () => {
    renderWithProviders(<IptvCountriesCard />);
    await userEvent.type(screen.getByTestId("iptv-search"), "germ");
    const select = screen.getByTestId("iptv-country") as HTMLSelectElement;
    const labels = Array.from(select.options).map((o) => o.textContent ?? "");
    expect(labels.some((l) => /Germany/.test(l))).toBe(true);
    expect(labels.some((l) => /United States/.test(l))).toBe(false);
  });

  it("disables apply when no country is selected", () => {
    renderWithProviders(<IptvCountriesCard />);
    expect(screen.getByTestId("iptv-apply")).toBeDisabled();
  });

  it("enables apply once a country with a default M3U is picked", async () => {
    renderWithProviders(<IptvCountriesCard />);
    const select = screen.getByTestId("iptv-country") as HTMLSelectElement;
    await userEvent.selectOptions(select, "US");
    expect(screen.getByTestId("iptv-apply")).not.toBeDisabled();
  });

  it("falls back to browse-only for countries with no M3U", async () => {
    renderWithProviders(<IptvCountriesCard />);
    const select = screen.getByTestId("iptv-country") as HTMLSelectElement;
    await userEvent.selectOptions(select, "FR");
    expect(screen.getByTestId("iptv-apply")).toBeDisabled();
    expect(screen.getByTestId("iptv-readonly-note")).toBeInTheDocument();
  });

  it("appends the chosen country's M3U to tuners[] when applied", async () => {
    renderWithProviders(<IptvCountriesCard />);
    const select = screen.getByTestId("iptv-country") as HTMLSelectElement;
    await userEvent.selectOptions(select, "US");
    await userEvent.click(screen.getByTestId("iptv-apply"));
    expect(saveMutate).toHaveBeenCalledOnce();
    const [body] = saveMutate.mock.calls[0]!;
    // The save body uses the new tuners/guides split — `m3u_url` lands
    // in `tuners[]` as `{name, url}`.
    const tuners = (body as { tuners: { url: string }[] }).tuners;
    expect(tuners).toHaveLength(1);
    expect(tuners[0]?.url).toBe("https://x/us.m3u");
  });
});
