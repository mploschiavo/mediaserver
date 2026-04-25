import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const queryState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const mutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  usePasswordPolicy: () => queryState,
  useUpdatePasswordPolicy: () => ({ mutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { PasswordPolicyCard } from "./PasswordPolicyCard";

// Real `/api/password-policy` payload after the v1.3.3 / v1.0.182
// expansion: explicit booleans + max_age_days + lockout fields. The
// legacy `require_classes` integer is kept on the read side.
const LIVE_PAYLOAD = {
  policy: {
    min_length: 14,
    require_uppercase: true,
    require_lowercase: true,
    require_digit: true,
    require_special: false,
    require_classes: 3,
    history_len: 5,
    max_age_days: 0,
    lockout_threshold: 5,
    lockout_window_minutes: 15,
  },
  bounds: {
    min_length: { floor: 4, ceiling: 128, default: 12 },
    require_classes: { floor: 1, ceiling: 4, default: 3 },
    history_len: { floor: 0, ceiling: 20, default: 5 },
    max_age_days: { floor: 0, ceiling: 365, default: 0 },
    lockout_threshold: { floor: 0, ceiling: 50, default: 5 },
    lockout_window_minutes: { floor: 0, ceiling: 1440, default: 15 },
  },
};

beforeEach(() => {
  queryState.data = undefined;
  queryState.isLoading = false;
  queryState.error = null;
  mutate.mockReset();
});

describe("PasswordPolicyCard", () => {
  it("renders skeletons while loading", () => {
    queryState.isLoading = true;
    renderWithProviders(<PasswordPolicyCard />);
    expect(screen.getByTestId("password-policy-loading")).toBeInTheDocument();
  });

  it("hydrates form values from the v1.3.3 payload", async () => {
    queryState.data = LIVE_PAYLOAD;
    renderWithProviders(<PasswordPolicyCard />);
    const minInput = (await screen.findByTestId(
      "pwp-min-length",
    )) as HTMLInputElement;
    await waitFor(() => expect(minInput.value).toBe("14"));
    const upper = (await screen.findByTestId(
      "pwp-require-uppercase",
    )) as HTMLButtonElement;
    expect(upper.getAttribute("aria-checked")).toBe("true");
    const special = (await screen.findByTestId(
      "pwp-require-special",
    )) as HTMLButtonElement;
    expect(special.getAttribute("aria-checked")).toBe("false");
  });

  it("derives the class-count summary from the toggles", async () => {
    queryState.data = LIVE_PAYLOAD;
    renderWithProviders(<PasswordPolicyCard />);
    const count = await screen.findByTestId("pwp-class-count");
    // 3 booleans on, 1 off ⇒ "3 of 4 classes required"
    await waitFor(() =>
      expect(count.textContent).toMatch(/3 of 4 classes required/),
    );
    // Toggle special → 4
    await userEvent.click(screen.getByTestId("pwp-require-special"));
    await waitFor(() =>
      expect(count.textContent).toMatch(/4 of 4 classes required/),
    );
    // Toggle uppercase off → 3
    await userEvent.click(screen.getByTestId("pwp-require-uppercase"));
    await waitFor(() =>
      expect(count.textContent).toMatch(/3 of 4 classes required/),
    );
  });

  it("derives toggles from legacy require_classes when booleans are missing", async () => {
    queryState.data = {
      policy: { min_length: 12, require_classes: 4, history_len: 5 },
      bounds: LIVE_PAYLOAD.bounds,
    };
    renderWithProviders(<PasswordPolicyCard />);
    // require_classes=4 ⇒ all four toggles on
    const upper = (await screen.findByTestId(
      "pwp-require-uppercase",
    )) as HTMLButtonElement;
    expect(upper.getAttribute("aria-checked")).toBe("true");
    const special = (await screen.findByTestId(
      "pwp-require-special",
    )) as HTMLButtonElement;
    expect(special.getAttribute("aria-checked")).toBe("true");
  });

  it("commits a slider change into the saved payload", async () => {
    queryState.data = LIVE_PAYLOAD;
    renderWithProviders(<PasswordPolicyCard />);
    const slider = (await screen.findByTestId(
      "pwp-min-length-slider",
    )) as HTMLInputElement;
    fireEvent.change(slider, { target: { value: "20" } });
    await userEvent.click(screen.getByTestId("pwp-save"));
    const [body] = mutate.mock.calls.at(-1)!;
    expect(body.min_length).toBe(20);
  });

  it("dispatches the v1.3.3 body on submit", async () => {
    queryState.data = LIVE_PAYLOAD;
    renderWithProviders(<PasswordPolicyCard />);
    await userEvent.click(await screen.findByTestId("pwp-save"));
    expect(mutate).toHaveBeenCalledOnce();
    const [body] = mutate.mock.calls[0]!;
    expect(body).toMatchObject({
      min_length: 14,
      require_uppercase: true,
      require_lowercase: true,
      require_digit: true,
      require_special: false,
      history_len: 5,
      max_age_days: 0,
      lockout_threshold: 5,
      lockout_window_minutes: 15,
    });
    // The deprecated integer is NOT echoed back — the booleans win.
    expect(body.require_classes).toBeUndefined();
  });

  it("propagates a toggle change into the saved body", async () => {
    queryState.data = LIVE_PAYLOAD;
    renderWithProviders(<PasswordPolicyCard />);
    await userEvent.click(await screen.findByTestId("pwp-require-special"));
    await userEvent.click(screen.getByTestId("pwp-save"));
    const [body] = mutate.mock.calls.at(-1)!;
    expect(body.require_special).toBe(true);
  });

  it("falls back to bounds defaults when the policy is empty", async () => {
    queryState.data = {
      policy: {},
      bounds: LIVE_PAYLOAD.bounds,
    };
    renderWithProviders(<PasswordPolicyCard />);
    const minInput = (await screen.findByTestId(
      "pwp-min-length",
    )) as HTMLInputElement;
    await waitFor(() => expect(minInput.value).toBe("12"));
    const lockout = (await screen.findByTestId(
      "pwp-lockout-window",
    )) as HTMLInputElement;
    expect(lockout.value).toBe("15");
  });

  it("applies bounds.* to slider min/max", async () => {
    queryState.data = LIVE_PAYLOAD;
    renderWithProviders(<PasswordPolicyCard />);
    const slider = (await screen.findByTestId(
      "pwp-min-length-slider",
    )) as HTMLInputElement;
    expect(slider.min).toBe("4");
    expect(slider.max).toBe("128");
    const ageSlider = (await screen.findByTestId(
      "pwp-max-age-days-slider",
    )) as HTMLInputElement;
    expect(ageSlider.max).toBe("365");
  });
});
