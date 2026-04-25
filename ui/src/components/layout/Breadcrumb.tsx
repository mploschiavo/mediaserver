import { Link, useLocation } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { Fragment, useMemo } from "react";
import { cn } from "@/lib/cn";
import { NAV_SECTIONS, SECONDARY_NAV } from "@/components/layout/Sidebar";

interface Crumb {
  label: string;
  to: string;
  current: boolean;
}

/**
 * Reads the active route from Tanstack Router and renders a
 * breadcrumb trail like "Media stack > Health > Media integrity".
 * The leading "Media stack" segment is always present; subsequent
 * segments derive from the section + nav-item the route belongs to.
 */
export function Breadcrumb() {
  const location = useLocation();
  const crumbs = useMemo<Crumb[]>(() => {
    const root: Crumb = { label: "Media Stack", to: "/", current: false };
    const trail: Crumb[] = [root];

    const all = [
      ...NAV_SECTIONS.flatMap((section) =>
        section.items.map((item) => ({ ...item, section: section.label })),
      ),
      ...SECONDARY_NAV.map((item) => ({ ...item, section: "Workspace" })),
    ];

    const match = all.find(
      (item) =>
        location.pathname === item.to ||
        location.pathname.startsWith(`${item.to}/`),
    );

    if (match) {
      trail.push({ label: match.section, to: match.to, current: false });
      trail.push({ label: match.label, to: match.to, current: true });
    } else {
      trail[trail.length - 1] = { ...trail[trail.length - 1]!, current: true };
    }
    return trail;
  }, [location.pathname]);

  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex items-center gap-1.5 truncate text-sm">
        {crumbs.map((crumb, idx) => (
          <Fragment key={`${crumb.to}-${idx}`}>
            {idx > 0 ? (
              <ChevronRight
                className="size-3.5 shrink-0 text-fg-faint"
                aria-hidden
              />
            ) : null}
            {crumb.current ? (
              <span
                className="truncate font-medium text-fg"
                aria-current="page"
              >
                {crumb.label}
              </span>
            ) : (
              <Link
                to={crumb.to}
                className={cn(
                  "truncate text-fg-muted transition-colors hover:text-fg",
                  "outline-none focus-visible:ring-2 focus-visible:ring-ring rounded",
                )}
              >
                {crumb.label}
              </Link>
            )}
          </Fragment>
        ))}
      </ol>
    </nav>
  );
}
