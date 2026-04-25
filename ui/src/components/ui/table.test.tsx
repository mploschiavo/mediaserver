import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "./table";

describe("Table primitives", () => {
  it("renders the full composition with semantic elements", () => {
    render(
      <Table>
        <TableCaption>Items</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Qty</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>Apple</TableCell>
            <TableCell>2</TableCell>
          </TableRow>
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell>Total</TableCell>
            <TableCell>2</TableCell>
          </TableRow>
        </TableFooter>
      </Table>,
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Apple" })).toBeInTheDocument();
    expect(screen.getByText("Items")).toBeInTheDocument();
  });

  it("Table applies caption-bottom + text-sm tokens", () => {
    render(<Table data-testid="t" />);
    const t = screen.getByTestId("t");
    expect(t.tagName).toBe("TABLE");
    expect(t.className).toContain("caption-bottom");
    expect(t.className).toContain("text-sm");
  });

  it("forwards className on every subcomponent", () => {
    render(
      <Table className="t-mk" data-testid="t">
        <TableHeader className="th-mk" data-testid="th">
          <TableRow className="tr-mk" data-testid="tr">
            <TableHead className="head-mk" data-testid="head">
              H
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody className="tb-mk" data-testid="tb">
          <TableRow>
            <TableCell className="td-mk" data-testid="td">
              C
            </TableCell>
          </TableRow>
        </TableBody>
        <TableFooter className="tf-mk" data-testid="tf">
          <TableRow>
            <TableCell>F</TableCell>
          </TableRow>
        </TableFooter>
        <TableCaption className="cap-mk">cap</TableCaption>
      </Table>,
    );
    expect(screen.getByTestId("t").className).toContain("t-mk");
    expect(screen.getByTestId("th").className).toContain("th-mk");
    expect(screen.getByTestId("tr").className).toContain("tr-mk");
    expect(screen.getByTestId("head").className).toContain("head-mk");
    expect(screen.getByTestId("tb").className).toContain("tb-mk");
    expect(screen.getByTestId("td").className).toContain("td-mk");
    expect(screen.getByTestId("tf").className).toContain("tf-mk");
    expect(screen.getByText("cap").className).toContain("cap-mk");
  });

  it("forwards refs on every subcomponent", () => {
    const refs = {
      t: { current: null as HTMLTableElement | null },
      thead: { current: null as HTMLTableSectionElement | null },
      tbody: { current: null as HTMLTableSectionElement | null },
      tfoot: { current: null as HTMLTableSectionElement | null },
      tr: { current: null as HTMLTableRowElement | null },
      th: { current: null as HTMLTableCellElement | null },
      td: { current: null as HTMLTableCellElement | null },
      cap: { current: null as HTMLTableCaptionElement | null },
    };
    render(
      <Table ref={refs.t}>
        <TableCaption ref={refs.cap}>cap</TableCaption>
        <TableHeader ref={refs.thead}>
          <TableRow ref={refs.tr}>
            <TableHead ref={refs.th}>h</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody ref={refs.tbody}>
          <TableRow>
            <TableCell ref={refs.td}>c</TableCell>
          </TableRow>
        </TableBody>
        <TableFooter ref={refs.tfoot}>
          <TableRow>
            <TableCell>f</TableCell>
          </TableRow>
        </TableFooter>
      </Table>,
    );
    expect(refs.t.current?.tagName).toBe("TABLE");
    expect(refs.thead.current?.tagName).toBe("THEAD");
    expect(refs.tbody.current?.tagName).toBe("TBODY");
    expect(refs.tfoot.current?.tagName).toBe("TFOOT");
    expect(refs.tr.current?.tagName).toBe("TR");
    expect(refs.th.current?.tagName).toBe("TH");
    expect(refs.td.current?.tagName).toBe("TD");
    expect(refs.cap.current?.tagName).toBe("CAPTION");
  });

  it("TableHead is left-aligned with uppercase tracking", () => {
    render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead data-testid="h">Name</TableHead>
          </TableRow>
        </TableHeader>
      </Table>,
    );
    const h = screen.getByTestId("h");
    expect(h.className).toContain("text-left");
    expect(h.className).toContain("uppercase");
  });
});
