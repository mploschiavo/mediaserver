import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const mutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useBulkImportUsers: () => ({ mutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { BulkImportDialog } from "./BulkImportDialog";

beforeEach(() => {
  mutate.mockReset();
});

describe("BulkImportDialog", () => {
  it("renders the trigger button", () => {
    renderWithProviders(<BulkImportDialog />);
    expect(screen.getByTestId("bulk-import-trigger")).toBeInTheDocument();
  });

  it("opens the dialog with a disabled Import button", async () => {
    renderWithProviders(<BulkImportDialog />);
    await userEvent.click(screen.getByTestId("bulk-import-trigger"));
    expect(await screen.findByTestId("bulk-import-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-import-submit")).toBeDisabled();
  });

  it("parses a CSV and previews up to 5 rows", async () => {
    renderWithProviders(<BulkImportDialog />);
    await userEvent.click(screen.getByTestId("bulk-import-trigger"));
    const input = (await screen.findByTestId(
      "bulk-import-file",
    )) as HTMLInputElement;
    const csv = [
      "username,email,role_slug",
      "alice,a@x.test,admin",
      "bob,b@x.test,viewer",
    ].join("\n");
    const file = new File([csv], "users.csv", { type: "text/csv" });
    await userEvent.upload(input, file);
    await waitFor(() =>
      expect(screen.getByTestId("bulk-import-preview")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("bulk-import-name")).toHaveTextContent(
      "users.csv",
    );
    expect(screen.getByTestId("bulk-import-submit")).not.toBeDisabled();
  });

  it("dispatches the import mutation with the parsed rows", async () => {
    renderWithProviders(<BulkImportDialog />);
    await userEvent.click(screen.getByTestId("bulk-import-trigger"));
    const input = (await screen.findByTestId(
      "bulk-import-file",
    )) as HTMLInputElement;
    const csv = "username,email\nalice,a@x.test";
    const file = new File([csv], "users.csv", { type: "text/csv" });
    await userEvent.upload(input, file);
    await waitFor(() =>
      expect(screen.getByTestId("bulk-import-submit")).not.toBeDisabled(),
    );
    await userEvent.click(screen.getByTestId("bulk-import-submit"));
    expect(mutate).toHaveBeenCalledOnce();
    const [body] = mutate.mock.calls[0]!;
    expect(body.rows[0]).toMatchObject({
      username: "alice",
      email: "a@x.test",
    });
  });
});
