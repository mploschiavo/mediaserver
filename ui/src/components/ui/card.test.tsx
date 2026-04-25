import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "./card";

describe("Card primitives", () => {
  it("renders Card with default token classes", () => {
    render(<Card data-testid="card">body</Card>);
    const el = screen.getByTestId("card");
    expect(el.tagName).toBe("DIV");
    expect(el.className).toContain("rounded-lg");
    expect(el.className).toContain("border-border");
    expect(el.className).toContain("bg-card");
  });

  it("forwards className on Card without dropping defaults", () => {
    render(<Card data-testid="card" className="extra-x" />);
    const el = screen.getByTestId("card");
    expect(el.className).toContain("extra-x");
    expect(el.className).toContain("bg-card");
  });

  it("forwards refs for every Card subcomponent", () => {
    const refs = {
      card: { current: null as HTMLDivElement | null },
      header: { current: null as HTMLDivElement | null },
      title: { current: null as HTMLDivElement | null },
      description: { current: null as HTMLDivElement | null },
      content: { current: null as HTMLDivElement | null },
      footer: { current: null as HTMLDivElement | null },
    };
    render(
      <Card ref={refs.card}>
        <CardHeader ref={refs.header}>
          <CardTitle ref={refs.title}>Title</CardTitle>
          <CardDescription ref={refs.description}>Desc</CardDescription>
        </CardHeader>
        <CardContent ref={refs.content}>Body</CardContent>
        <CardFooter ref={refs.footer}>Foot</CardFooter>
      </Card>,
    );
    expect(refs.card.current?.tagName).toBe("DIV");
    expect(refs.header.current?.tagName).toBe("DIV");
    expect(refs.title.current?.tagName).toBe("DIV");
    expect(refs.description.current?.tagName).toBe("DIV");
    expect(refs.content.current?.tagName).toBe("DIV");
    expect(refs.footer.current?.tagName).toBe("DIV");
  });

  it("renders the full composition with content", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Title</CardTitle>
          <CardDescription>Description</CardDescription>
        </CardHeader>
        <CardContent>Body text</CardContent>
        <CardFooter>Footer text</CardFooter>
      </Card>,
    );
    expect(screen.getByText("Title")).toBeInTheDocument();
    expect(screen.getByText("Description")).toBeInTheDocument();
    expect(screen.getByText("Body text")).toBeInTheDocument();
    expect(screen.getByText("Footer text")).toBeInTheDocument();
  });

  it("CardHeader pins gap + padding tokens", () => {
    render(<CardHeader data-testid="h" />);
    const el = screen.getByTestId("h");
    expect(el.className).toContain("flex");
    expect(el.className).toContain("p-6");
  });

  it("CardTitle uses semibold + tracking-tight text", () => {
    render(<CardTitle data-testid="t">x</CardTitle>);
    expect(screen.getByTestId("t").className).toContain("font-semibold");
    expect(screen.getByTestId("t").className).toContain("tracking-tight");
  });

  it("CardDescription uses muted text token", () => {
    render(<CardDescription data-testid="d">x</CardDescription>);
    expect(screen.getByTestId("d").className).toContain("text-fg-muted");
  });

  it("CardContent has p-6 pt-0 layout", () => {
    render(<CardContent data-testid="c">x</CardContent>);
    expect(screen.getByTestId("c").className).toContain("p-6");
    expect(screen.getByTestId("c").className).toContain("pt-0");
  });

  it("CardFooter renders as a flex row", () => {
    render(<CardFooter data-testid="f">x</CardFooter>);
    expect(screen.getByTestId("f").className).toContain("flex");
    expect(screen.getByTestId("f").className).toContain("items-center");
  });

  it("subcomponents accept extra className", () => {
    render(
      <>
        <CardHeader data-testid="h" className="ha" />
        <CardTitle data-testid="t" className="ta">
          T
        </CardTitle>
        <CardDescription data-testid="d" className="da">
          D
        </CardDescription>
        <CardContent data-testid="c" className="ca" />
        <CardFooter data-testid="f" className="fa" />
      </>,
    );
    expect(screen.getByTestId("h").className).toContain("ha");
    expect(screen.getByTestId("t").className).toContain("ta");
    expect(screen.getByTestId("d").className).toContain("da");
    expect(screen.getByTestId("c").className).toContain("ca");
    expect(screen.getByTestId("f").className).toContain("fa");
  });
});
