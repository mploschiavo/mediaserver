import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const mutate = vi.hoisted(() => vi.fn());
const isPending = vi.hoisted(() => ({ value: false }));

vi.mock("./hooks", () => ({
  useAddUser: () => ({ mutate, isPending: isPending.value }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { AddUserDialog } from "./AddUserDialog";

beforeEach(() => {
  mutate.mockReset();
  isPending.value = false;
});

describe("AddUserDialog", () => {
  it("renders the trigger button", () => {
    renderWithProviders(<AddUserDialog />);
    expect(screen.getByTestId("add-user-trigger")).toBeInTheDocument();
  });

  it("opens the dialog and renders the form fields", async () => {
    renderWithProviders(<AddUserDialog />);
    await userEvent.click(screen.getByTestId("add-user-trigger"));
    expect(await screen.findByTestId("add-user-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("add-user-username")).toBeInTheDocument();
    expect(screen.getByTestId("add-user-email")).toBeInTheDocument();
    expect(screen.getByTestId("add-user-password")).toBeInTheDocument();
  });

  it("blocks submit when username is empty", async () => {
    renderWithProviders(<AddUserDialog />);
    await userEvent.click(screen.getByTestId("add-user-trigger"));
    const submit = await screen.findByTestId("add-user-submit");
    expect(submit).toBeDisabled();
  });

  it("dispatches the create mutation with the typed values", async () => {
    renderWithProviders(<AddUserDialog />);
    await userEvent.click(screen.getByTestId("add-user-trigger"));
    await userEvent.type(
      await screen.findByTestId("add-user-username"),
      "alice",
    );
    await userEvent.type(screen.getByTestId("add-user-email"), "a@x.test");
    await userEvent.click(screen.getByTestId("add-user-submit"));
    expect(mutate).toHaveBeenCalledOnce();
    const [body] = mutate.mock.calls[0]!;
    expect(body).toMatchObject({
      username: "alice",
      email: "a@x.test",
      role_slug: "viewer",
    });
  });

  it("shows the loading state while pending", async () => {
    isPending.value = true;
    renderWithProviders(<AddUserDialog />);
    await userEvent.click(screen.getByTestId("add-user-trigger"));
    const submit = await screen.findByTestId("add-user-submit");
    // type=submit + loading attribute drives the spinner; the
    // disabled flag mirrors `loading` per the Button cva.
    expect(submit).toHaveAttribute("data-loading", "true");
  });
});
