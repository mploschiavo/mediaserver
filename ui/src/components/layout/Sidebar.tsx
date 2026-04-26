import { Link, useLocation } from "@tanstack/react-router";
import {
  Activity,
  Ban,
  BookOpen,
  Camera,
  FileText,
  GaugeCircle,
  Github,
  KeyRound,
  Layers,
  type LucideIcon,
  Route as RouteIcon,
  ScrollText,
  Settings,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Tv,
  UserCircle2,
  Users,
  Webhook,
  Workflow,
  Wrench,
} from "lucide-react";
import { motion } from "framer-motion";
import { useBranding, type BrandingShape } from "@/api";
import { Kbd, formatShortcut } from "@/lib/keyboard";
import { cn } from "@/lib/cn";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  badge?: number | "dot";
  shortcut?: string;
}

interface NavSection {
  label: string;
  items: NavItem[];
}

const PRIMARY_SECTIONS: NavSection[] = [
  {
    // Media-only — what's actually in the operator's library. Logs
    // and Routing previously lived here for historical reasons but
    // they're operations/network concerns, not library concerns.
    label: "Library",
    items: [
      { to: "/content", label: "Content", icon: Layers, shortcut: "g c" },
      { to: "/livetv", label: "Live TV", icon: Tv },
    ],
  },
  {
    // Edge gateway / DNS / TLS — promoted to its own group as the
    // /routing page grew rich enough (Envoy admin-summary, latency
    // percentiles, top-traffic, slowest-p99) to merit a dedicated
    // navigation slot. URL stays /routing for muscle memory.
    label: "Network",
    items: [
      { to: "/routing", label: "Edge gateway", icon: RouteIcon, shortcut: "g r" },
    ],
  },
  {
    label: "Operations",
    items: [
      { to: "/ops", label: "Ops", icon: Wrench, shortcut: "g o" },
      { to: "/guardrails", label: "Guardrails", icon: Shield },
      { to: "/webhooks", label: "Webhooks", icon: Webhook, shortcut: "g w" },
      { to: "/snapshots", label: "Snapshots", icon: Camera },
    ],
  },
  {
    label: "Identity",
    items: [
      { to: "/users", label: "Users", icon: Users, shortcut: "g u" },
      { to: "/me", label: "Me", icon: UserCircle2, shortcut: "g a" },
      { to: "/auth", label: "Auth", icon: KeyRound },
    ],
  },
  {
    label: "Security",
    items: [
      { to: "/sessions", label: "Sessions", icon: Activity, shortcut: "g s" },
      { to: "/bans", label: "Bans", icon: Ban, shortcut: "g b" },
      { to: "/security", label: "Signals", icon: ShieldAlert },
    ],
  },
  {
    // Logs joins Jobs + Audit log here — all three are "what is the
    // system doing right now / has done lately" surfaces.
    label: "Observability",
    items: [
      { to: "/jobs", label: "Jobs", icon: Workflow },
      { to: "/logs", label: "Logs", icon: FileText, shortcut: "g l" },
      { to: "/audit-log", label: "Audit log", icon: ScrollText },
    ],
  },
  {
    label: "Health",
    items: [
      {
        to: "/media-integrity",
        label: "Media integrity",
        icon: ShieldCheck,
        shortcut: "g m",
      },
      { to: "/profile", label: "Profile", icon: GaugeCircle, shortcut: "g p" },
    ],
  },
];

const SECONDARY_ITEMS: NavItem[] = [
  { to: "/api-docs", label: "API docs", icon: BookOpen },
  { to: "/settings", label: "Settings", icon: Settings },
];

interface SidebarProps {
  onNavigate?: () => void;
}

/**
 * Left-rail navigation. Renders grouped nav items with active-state
 * styling driven by Tanstack Router's `useLocation`. The logo +
 * product mark sit at the top, settings + docs sit at the bottom.
 *
 * `onNavigate` lets parent surfaces (e.g. mobile drawer) close the
 * sheet after a successful navigation.
 */
export function Sidebar({ onNavigate }: SidebarProps) {
  return (
    <motion.aside
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      className="flex h-full w-60 flex-col border-r border-border bg-bg-1"
    >
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <BrandMark />
      </div>
      <nav
        aria-label="Primary"
        className="flex-1 overflow-y-auto px-3 py-4"
      >
        {PRIMARY_SECTIONS.map((section) => (
          <NavSectionBlock
            key={section.label}
            section={section}
            onNavigate={onNavigate}
          />
        ))}
      </nav>
      <div
        className="border-t border-border px-3 py-3"
        aria-label="Secondary"
      >
        {SECONDARY_ITEMS.map((item) => (
          <NavLink key={item.to} item={item} onNavigate={onNavigate} />
        ))}
        <a
          href="https://github.com/mploschiavo/mediaserver"
          target="_blank"
          rel="noreferrer noopener"
          className={cn(
            "group flex items-center gap-3 rounded-md px-3 py-2 text-sm text-fg-muted transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out)]",
            "hover:bg-bg-2 hover:text-fg",
          )}
        >
          <Github className="size-4 shrink-0" aria-hidden />
          <span>Source</span>
        </a>
      </div>
    </motion.aside>
  );
}

/**
 * Read the live branding payload (`GET /api/branding`) and surface
 * the operator-facing wordmark + icon. The real wire shape exposes
 * `brand.name` and `brand.icon`/`brand.wordmark` (URLs to SVGs the
 * controller serves out of `/api/static/`); we render the icon when
 * available, falling back to the legacy `Activity` glyph + "Media
 * Stack" label so the chrome is never empty during the initial fetch.
 */
// Bundled fallback icon — served directly by the SPA, doesn't go
// through the controller's /api/static/ path (which now returns
// 410 Gone since the UI moved to its own container). Without this
// the post-login sidebar showed an empty space where the icon
// should be: the BrandMark requested ``/api/static/iomio-icon.svg``,
// the controller returned 410, the <img> failed silently.
const BUNDLED_ICON_URL = "/icons/iomio-icon.svg";

function BrandMark() {
  const branding = useBranding();
  const brand = (branding.data as BrandingShape | undefined)?.brand as
    | (BrandingShape["brand"] & {
        name?: string;
        icon?: string;
        vendor?: string;
      })
    | undefined;
  // Naming model (matches the controller's branding defaults):
  //   * ``brand.name``   — product short ("Media Stack")
  //   * ``brand.vendor`` — company ("iomio") shown as a small "by …"
  //     subtitle, never the primary wordmark.
  //   * ``brand.icon``   — square SVG. We render it WITHOUT the
  //     bg-accent backdrop because most icons carry their own
  //     visual weight; the box-with-icon was the "boring green box
  //     after login" the operator flagged.
  //
  // Icon URL resolution: any controller-supplied URL that points
  // at the legacy ``/api/static/`` path resolves to the bundled
  // SPA asset instead, since the controller no longer serves
  // those (returns 410 Gone since v1.0.175). White-label deploys
  // can still set an absolute URL or a UI-served path.
  const productName =
    (brand && typeof brand.name === "string" && brand.name) ||
    (brand && typeof brand.product_name === "string" && brand.product_name) ||
    "Media Stack";
  const vendor =
    (brand && typeof brand.vendor === "string" && brand.vendor) || "";
  const rawIcon =
    (brand && typeof brand.icon === "string" && brand.icon) ||
    (brand && typeof brand.logo_url === "string" && brand.logo_url) ||
    null;
  const iconUrl =
    rawIcon && rawIcon.startsWith("/api/static/")
      ? BUNDLED_ICON_URL
      : (rawIcon ?? BUNDLED_ICON_URL);

  return (
    <Link
      to="/"
      className="flex items-center gap-2 text-fg outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-md"
      data-testid="sidebar-brand"
    >
      <img
        src={iconUrl}
        alt=""
        aria-hidden
        className="size-7 object-contain"
        data-testid="sidebar-brand-icon"
        onError={(e) => {
          // If the configured URL fails (network blip, white-label
          // typo), fall back to the bundled asset — never show a
          // broken-image glyph in the chrome.
          const img = e.currentTarget;
          if (img.src !== BUNDLED_ICON_URL) img.src = BUNDLED_ICON_URL;
        }}
      />
      {/* keep Activity fallback only for the screen-reader
          alternate; the bundled SVG is always reachable so the box
          is no longer needed in practice */}
      <span className="flex flex-col leading-none">
        <span
          className="text-sm font-semibold tracking-tight"
          data-testid="sidebar-brand-name"
        >
          {productName}
        </span>
        {vendor ? (
          <span
            className="mt-0.5 text-[10px] text-fg-faint"
            data-testid="sidebar-brand-vendor"
          >
            by {vendor}
          </span>
        ) : null}
      </span>
    </Link>
  );
}

function NavSectionBlock({
  section,
  onNavigate,
}: {
  section: NavSection;
  onNavigate?: () => void;
}) {
  return (
    <div className="mb-5">
      <div className="px-3 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-fg-faint">
        {section.label}
      </div>
      <ul className="flex flex-col gap-0.5">
        {section.items.map((item) => (
          <li key={item.to}>
            <NavLink item={item} onNavigate={onNavigate} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function NavLink({
  item,
  onNavigate,
}: {
  item: NavItem;
  onNavigate?: () => void;
}) {
  const location = useLocation();
  const active =
    location.pathname === item.to ||
    location.pathname.startsWith(`${item.to}/`);
  const Icon = item.icon;
  return (
    <Link
      to={item.to}
      onClick={onNavigate}
      className={cn(
        "group relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium leading-3 outline-none transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out)]",
        "focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "bg-bg-2 text-fg"
          : "text-fg-muted hover:bg-bg-2/60 hover:text-fg",
      )}
    >
      {active ? (
        <motion.span
          layoutId="sidebar-active"
          className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-accent"
          transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          aria-hidden
        />
      ) : null}
      <Icon
        className={cn(
          "size-4 shrink-0 transition-colors",
          active ? "text-accent" : "text-fg-faint group-hover:text-fg-muted",
        )}
        aria-hidden
      />
      <span className="flex-1 truncate">{item.label}</span>
      {item.badge === "dot" ? (
        <span
          className="size-1.5 rounded-full bg-accent"
          aria-label="updates available"
        />
      ) : typeof item.badge === "number" ? (
        <span className="rounded-full bg-bg-3 px-1.5 py-0.5 text-[10px] font-semibold text-fg">
          {item.badge}
        </span>
      ) : null}
      {item.shortcut ? (
        <Kbd className="ml-1 hidden text-[10px] opacity-0 transition-opacity group-hover:opacity-100 xl:inline-flex">
          {formatShortcut(item.shortcut)}
        </Kbd>
      ) : null}
    </Link>
  );
}

export const NAV_SECTIONS = PRIMARY_SECTIONS;
export const SECONDARY_NAV = SECONDARY_ITEMS;
