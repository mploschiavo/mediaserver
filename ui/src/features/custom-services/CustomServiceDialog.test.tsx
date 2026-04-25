import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const defineMutate = vi.hoisted(() => vi.fn());
const defineState = vi.hoisted(() => ({ isPending: false }));

const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useDefineCustomService: () => ({
    mutate: defineMutate,
    ...defineState,
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import {
  CustomServiceDialog,
  isValidImageRef,
  isValidServiceName,
} from "./CustomServiceDialog";

describe("isValidServiceName", () => {
  it("accepts lowercase, digits, and dashes", () => {
    expect(isValidServiceName("my-service")).toBe(true);
    expect(isValidServiceName("svc-01")).toBe(true);
  });
  it("rejects empty input", () => {
    expect(isValidServiceName("")).toBe(false);
    expect(isValidServiceName("   ")).toBe(false);
  });
  it("rejects uppercase or spaces", () => {
    expect(isValidServiceName("MyService")).toBe(false);
    expect(isValidServiceName("my service")).toBe(false);
  });
  it("rejects underscores or dots", () => {
    expect(isValidServiceName("my_service")).toBe(false);
    expect(isValidServiceName("my.service")).toBe(false);
  });
});

describe("isValidImageRef", () => {
  it("accepts plain repo names", () => {
    expect(isValidImageRef("alpine")).toBe(true);
  });
  it("accepts namespace/repo", () => {
    expect(isValidImageRef("linuxserver/foo")).toBe(true);
  });
  it("accepts namespace/repo:tag", () => {
    expect(isValidImageRef("linuxserver/foo:latest")).toBe(true);
    expect(isValidImageRef("linuxserver/foo:1.2.3")).toBe(true);
  });
  it("accepts digest references", () => {
    expect(
      isValidImageRef(
        "linuxserver/foo@sha256:" + "a".repeat(64),
      ),
    ).toBe(true);
  });
  it("rejects whitespace", () => {
    expect(isValidImageRef("foo bar")).toBe(false);
  });
  it("rejects empty", () => {
    expect(isValidImageRef("")).toBe(false);
  });
});

describe("CustomServiceDialog", () => {
  beforeEach(() => {
    defineMutate.mockReset();
    defineState.isPending = false;
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not call mutate when name and image are invalid", async () => {
    const onOpenChange = vi.fn();
    renderWithProviders(
      <CustomServiceDialog open onOpenChange={onOpenChange} />,
    );
    await userEvent.type(
      screen.getByTestId("custom-service-name-input"),
      "Bad Name",
    );
    await userEvent.type(
      screen.getByTestId("custom-service-image-input"),
      "what is this",
    );
    await userEvent.click(screen.getByTestId("custom-service-submit"));
    expect(
      screen.getByTestId("custom-service-name-error"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("custom-service-image-error"),
    ).toBeInTheDocument();
    expect(defineMutate).not.toHaveBeenCalled();
  });

  it("submits a valid form and reports success", async () => {
    const onOpenChange = vi.fn();
    defineMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(
      <CustomServiceDialog open onOpenChange={onOpenChange} />,
    );
    await userEvent.type(
      screen.getByTestId("custom-service-name-input"),
      "my-svc",
    );
    await userEvent.type(
      screen.getByTestId("custom-service-image-input"),
      "linuxserver/foo:latest",
    );
    await userEvent.type(
      screen.getByTestId("custom-service-ports-input"),
      "8080:80",
    );
    await userEvent.click(screen.getByTestId("custom-service-submit"));
    await waitFor(() => expect(defineMutate).toHaveBeenCalledTimes(1));
    expect(defineMutate.mock.calls[0]?.[0]).toMatchObject({
      name: "my-svc",
      image: "linuxserver/foo:latest",
      ports: "8080:80",
    });
    expect(toastSuccess).toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("disables submit until both name and image are typed", () => {
    renderWithProviders(
      <CustomServiceDialog open onOpenChange={() => {}} />,
    );
    expect(screen.getByTestId("custom-service-submit")).toBeDisabled();
  });

  it("reports an error toast when the mutation fails", async () => {
    defineMutate.mockImplementation(
      (
        _v: unknown,
        opts: { onError: (e: Error) => void },
      ) => opts.onError(new Error("boom")),
    );
    renderWithProviders(
      <CustomServiceDialog open onOpenChange={() => {}} />,
    );
    await userEvent.type(
      screen.getByTestId("custom-service-name-input"),
      "ok-svc",
    );
    await userEvent.type(
      screen.getByTestId("custom-service-image-input"),
      "alpine:3.20",
    );
    await userEvent.click(screen.getByTestId("custom-service-submit"));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        expect.stringContaining("boom"),
      ),
    );
  });
});
