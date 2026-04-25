import { Link, useRouterState } from "@tanstack/react-router";
import {
  Library,
  type LucideIcon,
  ScrollText,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { motion } from "framer-motion";
import { cn } from "@/lib/cn";

interface BottomNavItem {
  to: string;
  label: string;
  Icon: LucideIcon;
}

const ITEMS: ReadonlyArray<BottomNavItem> = [
  { to: "/content", label: "Library", Icon: Library },
  { to: "/logs", label: "Logs", Icon: ScrollText },
  { to: "/ops", label: "Ops", Icon: Wrench },
  { to: "/media-integrity", label: "Health", Icon: ShieldCheck },
];

/**
 * Mobile-only fixed bottom navigation. Surfaces the four highest
 * priority routes (Library, Logs, Ops, Health) at thumb-reach so the
 * full sidebar tree can stay tucked behind the Vaul drawer.
 *
 * Each item is a 44x44 touch target with a vertical icon + label
 * stack. The active item gets an accent text color plus a small
 * sliding pill rendered via Framer Motion's shared `layoutId`, so
 * tapping between items animates a single underline rather than four
 * independent fades.
 */
export function BottomNav() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  return (
    <nav
      className={cn(
        "fixed inset-x-0 bottom-0 z-40 flex h-14 border-t border-border",
        "bg-bg-1/85 backdrop-blur-md md:hidden",
      )}
      style={{ paddingBottom: "var(--safe-area-bottom)" }}
      aria-label="Primary"
      data-testid="bottom-nav"
    >
      {ITEMS.map(({ to, label, Icon }) => {
        const active =
          pathname === to || pathname.startsWith(`${to}/`);
        return (
          <Link
            key={to}
            to={to}
            className={cn(
              "relative flex flex-1 flex-col items-center justify-center gap-0.5",
              "min-h-[44px] min-w-[44px] text-[11px] font-medium outline-none",
              "transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out)]",
              "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
              active ? "text-accent" : "text-fg-muted hover:text-fg",
            )}
            aria-current={active ? "page" : undefined}
          >
            {active ? (
              <motion.span
                layoutId="bottom-nav-active"
                className="absolute inset-x-3 top-1 h-1 rounded-full bg-accent"
                transition={{ type: "spring", stiffness: 380, damping: 30 }}
                aria-hidden
              />
            ) : null}
            <Icon
              className={cn(
                "size-6 transition-colors",
                active ? "text-accent" : "text-fg-faint",
              )}
              aria-hidden
            />
            <span>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export const BOTTOM_NAV_ITEMS = ITEMS;
