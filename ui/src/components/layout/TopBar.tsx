import { AnimatePresence, motion } from "framer-motion";
import { Menu, Moon, Search, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import { ConnectionStatus } from "@/components/layout/ConnectionStatus";
import { UserMenu } from "@/components/layout/UserMenu";
import { useTheme } from "@/components/layout/ThemeProvider";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useIdentity } from "@/api/hooks";
import {
  useManifests,
  type ManifestsResponse,
} from "@/features/infra-detail/hooks";
import { asObjectMap } from "@/lib/coerce";
import { Kbd, formatShortcut } from "@/lib/keyboard";
import { cn } from "@/lib/cn";

interface TopBarProps {
  onOpenSidebar: () => void;
  onOpenCommand: () => void;
}

/**
 * Sticky page chrome above the main content. Houses the mobile
 * sidebar trigger, breadcrumb trail, the search-hint button that
 * opens the command palette, the live connection dot, theme
 * toggle, and account menu. Becomes semi-transparent + blurred
 * once the page scrolls so it floats above the content.
 */
export function TopBar({ onOpenSidebar, onOpenCommand }: TopBarProps) {
  const [scrolled, setScrolled] = useState(false);
  const identity = useIdentity();
  // Resolve the displayed identity from the controller. Fall back to
  // the literal "Signed in" while the request is in flight rather
  // than the misleading hard-coded "Operator/ops@local" stub.
  const displayName =
    identity.data?.display_name ??
    identity.data?.username ??
    identity.data?.user ??
    (identity.isLoading ? "Signed in" : "Guest");
  const displayEmail = identity.data?.email;

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 4);
    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <header
      className={cn(
        "sticky top-0 z-30 flex h-14 items-center gap-3 border-b px-4 transition-colors duration-200",
        scrolled
          ? "border-border bg-bg/70 backdrop-blur-md supports-[backdrop-filter]:bg-bg/60"
          : "border-transparent bg-bg",
      )}
    >
      <button
        type="button"
        onClick={onOpenSidebar}
        className="flex size-8 items-center justify-center rounded-md text-fg-muted outline-none transition-colors hover:bg-bg-2 hover:text-fg focus-visible:ring-2 focus-visible:ring-ring md:hidden"
        aria-label="Open navigation"
      >
        <Menu className="size-4" aria-hidden />
      </button>

      <Breadcrumb />

      <div className="flex flex-1 justify-center">
        <button
          type="button"
          onClick={onOpenCommand}
          className={cn(
            "hidden items-center gap-2 rounded-md border border-border bg-bg-1 px-2.5 py-1.5 text-xs text-fg-muted shadow-sm outline-none transition-colors",
            "hover:border-border-strong hover:text-fg",
            "focus-visible:ring-2 focus-visible:ring-ring",
            "md:inline-flex md:w-72",
          )}
        >
          <Search className="size-3.5" aria-hidden />
          <span className="flex-1 text-left">Press {formatShortcut("mod+k")} to search…</span>
          <Kbd className="text-[10px]">{formatShortcut("mod+k")}</Kbd>
        </button>
      </div>

      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={onOpenCommand}
          aria-label="Open command palette"
          className="flex size-8 items-center justify-center rounded-md text-fg-muted outline-none transition-colors hover:bg-bg-2 hover:text-fg focus-visible:ring-2 focus-visible:ring-ring md:hidden"
        >
          <Search className="size-4" aria-hidden />
        </button>
        <ConnectionStatus />
        <StackModeChip />
        <ThemeToggle />
        <UserMenu name={displayName} email={displayEmail} />
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Stack-mode chip — renders a tiny pill labelling the active deployment
// topology (Kubernetes vs Docker). Reads `/api/manifests` via the
// `useManifests` hook; stays defensive about the live shape so a 500 or
// an unexpected payload silently degrades to "no chip" rather than
// crashing the chrome.
// ---------------------------------------------------------------------------

interface StackModeMeta {
  kind: "kubernetes" | "docker";
  detail: string;
}

/**
 * Coerce the live `/api/manifests` payload into the chip's display
 * model. Returns `null` when the controller is unreachable, returns
 * an unknown / 500 response, or doesn't surface enough to label the
 * mode — in which case the chip is omitted entirely (per the
 * "unknown → no chip (defensive)" requirement).
 *
 * The v1.3.2 OpenAPI tightened `type` to a fixed enum; we coerce
 * via `asObjectMap` first so a payload that 500s into a string
 * doesn't blow up the chip.
 */
export function readStackMode(
  data: ManifestsResponse | undefined,
): StackModeMeta | null {
  if (!data) return null;
  const raw = asObjectMap(data);
  const type = typeof raw.type === "string" ? raw.type : "";
  if (type === "kubernetes") {
    const ns =
      typeof raw.namespace === "string" && raw.namespace
        ? raw.namespace
        : "media-stack";
    return { kind: "kubernetes", detail: ns };
  }
  if (
    type === "docker" ||
    type === "compose" ||
    type === "compose-runtime"
  ) {
    const project =
      (typeof raw.project_name === "string" && raw.project_name) ||
      (typeof raw.namespace === "string" && raw.namespace) ||
      "media-stack";
    return { kind: "docker", detail: project };
  }
  return null;
}

function StackModeChip() {
  const manifests = useManifests();
  // Defensive: any error or non-2xx → no chip. The TopBar chrome
  // must never crash if `/api/manifests` 500s.
  if (manifests.error) return null;
  const meta = readStackMode(manifests.data);
  if (!meta) return null;

  const isK8s = meta.kind === "kubernetes";
  const label = isK8s ? "K8s" : "Docker";
  const tooltip = isK8s
    ? `Kubernetes namespace: ${meta.detail}`
    : `Docker project: ${meta.detail}`;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid="stack-mode-chip"
          aria-label={tooltip}
          className={cn(
            "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium",
            isK8s
              ? "border-[color-mix(in_oklab,var(--color-info)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-info)_15%,transparent)] text-info"
              : "border-border bg-[color-mix(in_oklab,var(--color-fg)_8%,transparent)] text-fg-muted",
          )}
        >
          <span className="font-semibold">{label}</span>
          <span aria-hidden>·</span>
          <span className="font-mono text-[11px] tabular-nums">
            {meta.detail}
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent>{tooltip}</TooltipContent>
    </Tooltip>
  );
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      className="relative flex size-8 items-center justify-center rounded-md text-fg-muted outline-none transition-colors hover:bg-bg-2 hover:text-fg focus-visible:ring-2 focus-visible:ring-ring"
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={isDark ? "moon" : "sun"}
          initial={{ rotate: -45, opacity: 0, scale: 0.8 }}
          animate={{ rotate: 0, opacity: 1, scale: 1 }}
          exit={{ rotate: 45, opacity: 0, scale: 0.8 }}
          transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          className="absolute inset-0 flex items-center justify-center"
        >
          {isDark ? (
            <Moon className="size-4" aria-hidden />
          ) : (
            <Sun className="size-4" aria-hidden />
          )}
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
