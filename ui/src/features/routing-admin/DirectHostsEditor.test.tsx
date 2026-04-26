import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import {
  DirectHostsEditor,
  collectDirectHostErrors,
} from "./DirectHostsEditor";

describe("DirectHostsEditor", () => {
  it("renders empty-state copy when no rows are present", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <DirectHostsEditor value={{}} onChange={onChange} gatewayHost="m.example.com" />,
    );
    expect(screen.getByTestId("direct-hosts-editor")).toBeInTheDocument();
    expect(screen.getByTestId("direct-hosts-empty")).toBeInTheDocument();
  });

  it("renders one row per existing entry", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <DirectHostsEditor
        value={{ media_server: "jf.example.com", auth: "auth.example.com" }}
        onChange={onChange}
      />,
    );
    expect(screen.getByTestId("direct-host-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("direct-host-row-1")).toBeInTheDocument();
    const host0 = screen.getByTestId(
      "direct-host-row-0-host",
    ) as HTMLInputElement;
    expect(host0.value).toBe("jf.example.com");
  });

  it("emits a new map when the hostname changes", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <DirectHostsEditor
        value={{ media_server: "old.example.com" }}
        onChange={onChange}
      />,
    );
    const host = screen.getByTestId("direct-host-row-0-host");
    fireEvent.change(host, { target: { value: "new.example.com" } });
    expect(onChange).toHaveBeenLastCalledWith({
      media_server: "new.example.com",
    });
  });

  it("removes a row when the trash button is clicked", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <DirectHostsEditor
        value={{ media_server: "jf.example.com" }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByTestId("direct-host-row-0-remove"));
    expect(onChange).toHaveBeenLastCalledWith({});
  });

  it("appends a fresh well-known role when Add is clicked", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <DirectHostsEditor value={{}} onChange={onChange} />,
    );
    fireEvent.click(screen.getByTestId("direct-hosts-add"));
    // First well-known role is `media_server` — picked first because
    // no rows exist yet.
    expect(onChange).toHaveBeenLastCalledWith({ media_server: "" });
  });

  it("flags invalid hostnames", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <DirectHostsEditor
        value={{ media_server: "not a host" }}
        onChange={onChange}
      />,
    );
    expect(
      screen.getByTestId("direct-host-row-0-host-error"),
    ).toBeInTheDocument();
  });

  it("warns when a hostname is identical to the gateway", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <DirectHostsEditor
        value={{ media_server: "m.example.com" }}
        onChange={onChange}
        gatewayHost="m.example.com"
      />,
    );
    expect(
      screen.getByTestId("direct-host-row-0-host-warning"),
    ).toBeInTheDocument();
  });
});

describe("collectDirectHostErrors", () => {
  it("returns no errors for an empty map", () => {
    expect(collectDirectHostErrors({})).toEqual([]);
  });

  it("returns no errors for a valid map", () => {
    expect(
      collectDirectHostErrors({ media_server: "jf.example.com" }),
    ).toEqual([]);
  });

  it("flags malformed hostnames", () => {
    const errs = collectDirectHostErrors({ media_server: "no dots" });
    expect(errs.length).toBeGreaterThan(0);
    expect(errs.join("\n")).toMatch(/looks invalid/i);
  });

  it("flags duplicate hostnames mapped to different roles", () => {
    const errs = collectDirectHostErrors({
      media_server: "shared.example.com",
      auth: "shared.example.com",
    });
    expect(errs.join("\n")).toMatch(/mapped to more than one role/i);
  });
});
