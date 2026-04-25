import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";

const backMock = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  // The createRoute helper is invoked at module load to register the
  // splat; since we don't mount inside a router for this unit test,
  // stub it to a sentinel object that records the inputs.
  createRoute: (opts: { component: () => ReactElement }) => ({
    options: opts,
    component: opts.component,
  }),
  useRouter: () => ({ history: { back: backMock } }),
  Link: ({
    to,
    children,
    ...rest
  }: {
    to: string;
    children: ReactNode;
    [key: string]: unknown;
  }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/routes/__root", () => ({
  Route: { id: "__root_stub__" },
}));

import { Route as NotFoundRoute } from "./$";

const NotFound = (NotFoundRoute as unknown as { component: () => ReactElement })
  .component;

describe("NotFound (catchall)", () => {
  it("renders Lost your way + both buttons", () => {
    render(<NotFound />);
    expect(
      screen.getByRole("heading", { name: /lost your way/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /go home/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /back/i })).toBeInTheDocument();
  });

  it('"Go home" link points to "/"', () => {
    render(<NotFound />);
    expect(screen.getByRole("link", { name: /go home/i })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it('"Back" button calls router.history.back()', async () => {
    backMock.mockReset();
    render(<NotFound />);
    await userEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(backMock).toHaveBeenCalledOnce();
  });
});
