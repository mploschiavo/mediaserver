import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

// Real /api/livetv-sources payload (sourced from
// ui/.ratchets/notes/API-RESPONSE-SHAPES-2026-04-25.txt). Tuners and
// guides are PARALLEL arrays; the active pair is in the scalar
// `tuner_url` / `guide_url`.
const sourcesState = vi.hoisted(
  () =>
    ({
      data: undefined,
      isLoading: false,
      error: null,
    }) as {
      data:
        | {
            tuners?: readonly { url: string; name: string }[];
            guides?: readonly { url: string; name: string }[];
            tuner_url?: string;
            guide_url?: string;
          }
        | undefined;
      isLoading: boolean;
      error: Error | null;
    },
);

const saveMutate = vi.hoisted(() => vi.fn());
const savePending = vi.hoisted(() => ({ value: false }));

vi.mock("./hooks", () => ({
  useLivetvSources: () => sourcesState,
  useSaveLivetvSources: () => ({
    mutate: saveMutate,
    isPending: savePending.value,
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { LivetvSourcesCard } from "./LivetvSourcesCard";

beforeEach(() => {
  sourcesState.data = { tuners: [], guides: [] };
  sourcesState.isLoading = false;
  sourcesState.error = null;
  saveMutate.mockReset();
  savePending.value = false;
});

describe("LivetvSourcesCard", () => {
  it("renders the empty state when no tuners or guides are configured", () => {
    renderWithProviders(<LivetvSourcesCard />);
    expect(screen.getByText(/No live-TV sources/i)).toBeInTheDocument();
  });

  it("renders a loading skeleton while the query resolves", () => {
    sourcesState.isLoading = true;
    sourcesState.data = undefined;
    renderWithProviders(<LivetvSourcesCard />);
    expect(screen.getByTestId("livetv-sources-loading")).toBeInTheDocument();
  });

  it("renders an error message when the query fails", () => {
    sourcesState.error = new Error("livetv broken");
    sourcesState.data = undefined;
    renderWithProviders(<LivetvSourcesCard />);
    expect(screen.getByTestId("livetv-sources-error")).toHaveTextContent(
      "livetv broken",
    );
  });

  it("renders tuners and guides as parallel lists", () => {
    sourcesState.data = {
      tuners: [
        {
          url: "https://iptv-org.github.io/iptv/countries/us.m3u",
          name: "Default",
        },
      ],
      guides: [
        {
          url: "https://iptv-epg.org/files/epg-us.xml",
          name: "Default",
        },
      ],
      tuner_url: "https://iptv-org.github.io/iptv/countries/us.m3u",
      guide_url: "https://iptv-epg.org/files/epg-us.xml",
    };
    renderWithProviders(<LivetvSourcesCard />);
    expect(screen.getByTestId("livetv-tuner-heading")).toHaveTextContent(
      "Tuners (M3U)",
    );
    expect(screen.getByTestId("livetv-guide-heading")).toHaveTextContent(
      "Guides (XMLTV EPG)",
    );
    // Both rows show the same name; the active badge is rendered
    // for whichever URL matches `tuner_url` / `guide_url`.
    expect(screen.getAllByText("Default").length).toBeGreaterThanOrEqual(2);
    // Each list has its own "active" badge. ResponsiveTable mounts
    // both desktop + mobile branches under happy-dom, doubling badge
    // matches; assert at least 2 (one per list) rather than exact.
    expect(screen.getAllByText("active").length).toBeGreaterThanOrEqual(2);
  });

  it("opens the add dialog with kind + name + URL fields", async () => {
    renderWithProviders(<LivetvSourcesCard />);
    await userEvent.click(screen.getByTestId("livetv-add-trigger"));
    expect(await screen.findByTestId("livetv-add-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("livetv-kind")).toBeInTheDocument();
    expect(screen.getByTestId("livetv-name")).toBeInTheDocument();
    expect(screen.getByTestId("livetv-url")).toBeInTheDocument();
    expect(screen.getByTestId("livetv-submit")).toBeInTheDocument();
  });

  it("submits a tuners-only payload when adding a tuner", async () => {
    sourcesState.data = { tuners: [], guides: [] };
    renderWithProviders(<LivetvSourcesCard />);
    await userEvent.click(screen.getByTestId("livetv-add-trigger"));
    await userEvent.type(
      await screen.findByTestId("livetv-name"),
      "MyPack",
    );
    await userEvent.type(
      screen.getByTestId("livetv-url"),
      "https://example.com/p.m3u",
    );
    await userEvent.click(screen.getByTestId("livetv-submit"));
    expect(saveMutate).toHaveBeenCalledOnce();
    const [body] = saveMutate.mock.calls[0]!;
    expect(body).toHaveProperty("tuners");
    expect(body).not.toHaveProperty("guides");
    const list = (body as { tuners: { name: string; url: string }[] }).tuners;
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({
      name: "MyPack",
      url: "https://example.com/p.m3u",
    });
  });

  it("activates a tuner via Use button", async () => {
    sourcesState.data = {
      tuners: [
        { url: "https://a.example/p.m3u", name: "A" },
        { url: "https://b.example/p.m3u", name: "B" },
      ],
      guides: [],
      tuner_url: "https://a.example/p.m3u",
    };
    renderWithProviders(<LivetvSourcesCard />);
    // Only the non-active row exposes the Use button.
    await userEvent.click(
      screen.getByTestId("livetv-tuner-activate-B"),
    );
    expect(saveMutate).toHaveBeenCalledOnce();
    const [body] = saveMutate.mock.calls[0]!;
    expect(body).toEqual({ tuner_url: "https://b.example/p.m3u" });
  });

  it("confirms before deleting a tuner", async () => {
    sourcesState.data = {
      tuners: [{ url: "https://a.example/p.m3u", name: "A" }],
      guides: [],
    };
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithProviders(<LivetvSourcesCard />);
    await userEvent.click(screen.getByTestId("livetv-tuner-delete-A"));
    expect(confirmSpy).toHaveBeenCalledOnce();
    expect(saveMutate).toHaveBeenCalledOnce();
    const [body] = saveMutate.mock.calls[0]!;
    expect(body).toEqual({ tuners: [] });
    confirmSpy.mockRestore();
  });

  it("aborts delete when the operator cancels the confirm", async () => {
    sourcesState.data = {
      tuners: [{ url: "https://a.example/p.m3u", name: "A" }],
      guides: [],
    };
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithProviders(<LivetvSourcesCard />);
    await userEvent.click(screen.getByTestId("livetv-tuner-delete-A"));
    expect(saveMutate).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
