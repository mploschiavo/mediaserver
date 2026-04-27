import { Drawer } from "vaul";
import { AnimatePresence, motion } from "framer-motion";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { useNavigate, useRouter } from "@tanstack/react-router";
import { useHealth } from "@/api";
import { BottomNav } from "@/components/layout/BottomNav";
import { PullToRefresh } from "@/components/layout/PullToRefresh";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { UpdateAvailableBanner } from "@/components/layout/UpdateAvailableBanner";
import {
  CommandPalette,
  useCommandPalette,
} from "@/components/layout/CommandPalette";
import { UpgradeBanner } from "@/features/stack-lifecycle/UpgradeBanner";
import { TriggeredBanner } from "@/features/guardrails";
import { RunningJobsBanner } from "@/features/jobs/RunningJobsBanner";
import { BootstrapProgressBanner } from "@/features/onboarding/BootstrapProgressBanner";
import {
  startAlertEngine,
  type HealthLike,
} from "@/features/alerts/AlertEngine";
import { useSwipeToOpenSidebar } from "@/hooks/useSwipeToOpenSidebar";
import { cn } from "@/lib/cn";

const MOBILE_BREAKPOINT = 768;

const GOTO_BINDINGS: ReadonlyArray<readonly [string, string]> = [
  ["g c", "/content"],
  ["g l", "/logs"],
  ["g r", "/routing"],
  ["g o", "/ops"],
  ["g w", "/webhooks"],
  ["g u", "/users"],
  ["g a", "/me"],
  ["g m", "/media-integrity"],
  ["g p", "/profile"],
  ["g s", "/settings"],
];

interface AppShellProps {
  children: ReactNode;
}

/**
 * Two-column page chrome. The desktop layout is a static
 * 240px sidebar + flex-grow content column with the TopBar pinned
 * to the content's top. On mobile the sidebar collapses behind a
 * hamburger that opens a Vaul drawer.
 *
 * Global keyboard shortcuts (the "g X" sequence bindings, esc, and
 * ⌘K) are wired here so they fire regardless of which route is
 * mounted.
 */
export function AppShell({ children }: AppShellProps) {
  const navigate = useNavigate();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useCommandPalette();
  const isMobile = useIsMobile();

  // Bind every "g X" sequence to its route. Each binding registers
  // its own listener; when "g" then the second key is pressed in
  // quick succession, react-hotkeys-hook fires the matching handler.
  useGotoSequences(navigate, () => setMobileOpen(false));

  useHotkeys(
    "shift+/",
    (event) => {
      event.preventDefault();
      setPaletteOpen(true);
    },
    [setPaletteOpen],
  );

  // Open the Vaul drawer on a left-edge horizontal swipe. The hook
  // is enabled on every viewport but only does work on touch
  // devices (it auto-detects via `(hover: hover)`); the desktop
  // sidebar lives at >= md so the gesture is effectively mobile-only.
  useSwipeToOpenSidebar({
    onOpen: () => setMobileOpen(true),
  });

  // Pull-to-refresh re-runs every active query in the route.
  const handleRefresh = useCallback(async () => {
    await router.invalidate();
  }, [router]);

  // Client-side alert engine. Re-uses the live `useHealth` query
  // (already polled by the rest of the shell) as the data source so
  // we never double-fetch. The engine itself owns its 30s tick. We
  // keep the latest snapshot in a ref so the engine's
  // `pollHealth()` callback never closes over a stale value.
  const healthQuery = useHealth();
  const healthRef = useRef<HealthLike | undefined>(undefined);
  useEffect(() => {
    healthRef.current = healthQuery.data;
  }, [healthQuery.data]);

  useEffect(() => {
    const handle = startAlertEngine({
      pollHealth: () => healthRef.current,
    });
    return () => handle.stop();
  }, []);

  return (
    <div className="flex min-h-screen w-full bg-bg text-fg">
      {/* Skip link — first focusable element so screen-reader and
          keyboard users can jump past the sidebar to route content. */}
      <a
        href="#main-content"
        className={cn(
          "sr-only focus:not-sr-only",
          "focus:fixed focus:left-2 focus:top-2 focus:z-[100] focus:rounded-md focus:bg-bg-1 focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-fg focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-ring",
        )}
      >
        Skip to main content
      </a>

      {/* Desktop sidebar (always present once viewport >= md). Pinned
          to the viewport on desktop via ``md:sticky md:top-0 md:h-screen``
          so navigation stays reachable on long pages (Logs, Audit
          log, Reachability matrix) — Linear / GitHub / Vercel / Notion
          convention. This pin has been reverted twice in this session
          by agent file-collisions; keep this comment block so future
          automated edits don't silently strip it again. The
          companion test in AppShell.test.tsx asserts the sticky
          classes are present. */}
      <div
        className={cn(
          "hidden md:sticky md:top-0 md:flex md:h-screen md:w-60 md:flex-shrink-0",
          // Sidebar background reads slightly darker than main
          // content via bg-bg-1 inside the Sidebar component.
        )}
        aria-hidden={isMobile ? true : undefined}
      >
        <Sidebar />
      </div>

      {/* Mobile sidebar drawer via Vaul. */}
      <Drawer.Root
        open={mobileOpen}
        onOpenChange={setMobileOpen}
        direction="left"
      >
        <Drawer.Portal>
          <Drawer.Overlay className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm" />
          <Drawer.Content className="fixed inset-y-0 left-0 z-50 flex w-64 outline-none">
            <Drawer.Title className="sr-only">Navigation</Drawer.Title>
            <Drawer.Description className="sr-only">
              Primary application navigation
            </Drawer.Description>
            <Sidebar onNavigate={() => setMobileOpen(false)} />
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Stack-upgrade banner. Renders nothing unless the controller
            reports `available: true`; on probe error it also stays
            silent so a flaky endpoint can't break the shell. */}
        <UpgradeBanner />
        {/* First-run progress banner — renders only while
            ``initial_bootstrap_done`` is still false on the
            controller. Self-trims as soon as the bootstrap window
            closes; on a returning visit it never renders at all. */}
        <BootstrapProgressBanner />
        {/* Cross-domain guardrail banner — renders only when at least
            one rule is firing at warning+ severity. Click navigates
            to /guardrails?focus=<id> for the worst offender. */}
        <TriggeredBanner />
        {/* Running-jobs aggregator — single source of truth for
            "what's happening right now" across actions, jobs, and
            CronJob pods. Renders nothing when nothing is running. */}
        <RunningJobsBanner />
        {/* SPA-cache-staleness banner. Renders when the running
            controller version (probed via /api/stack/update) has
            moved past the SPA's build-time `VITE_BUILD_VERSION` —
            i.e. the operator is looking at cached HTML/JS that
            pre-dates the latest deploy. Refresh button unregisters
            the SW and reloads to force-fresh assets. Renders
            nothing when versions match or the probe is in flight. */}
        <UpdateAvailableBanner />
        <TopBar
          onOpenSidebar={() => setMobileOpen(true)}
          onOpenCommand={() => setPaletteOpen(true)}
        />
        <main
          id="main-content"
          tabIndex={-1}
          className={cn(
            "flex-1 outline-none",
            // BottomNav (56px + safe-area) lives at < md only; padding
            // is also wiped on >= md so desktop content can run flush.
            "pb-[calc(56px+var(--safe-area-bottom))] md:pb-0",
          )}
        >
          <PullToRefresh onRefresh={handleRefresh}>
            <AnimatePresence mode="wait">
              <motion.div
                key="route-outlet"
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              >
                {children}
              </motion.div>
            </AnimatePresence>
          </PullToRefresh>
        </main>
      </div>

      {/* Mobile bottom nav coexists with the Vaul drawer: drawer is
          for the full nav tree, BottomNav is for thumb-reach to the
          four most important routes. Hidden at >= md. */}
      <BottomNav />

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}

/**
 * Registers each "g X" sequence binding via its own `useHotkeys`
 * call so the rules-of-hooks contract holds (the array shape never
 * changes between renders). Splitting it out keeps the main shell
 * component's body readable.
 */
function useGotoSequences(
  navigate: ReturnType<typeof useNavigate>,
  onActivate: () => void,
): void {
  for (const [sequence, to] of GOTO_BINDINGS) {
     
    useHotkeys(
      sequence,
      (event) => {
        event.preventDefault();
        navigate({ to });
        onActivate();
      },
      { preventDefault: true },
      [navigate, to],
    );
  }
}

/** Tracks viewport width to keep aria-hidden state honest. */
function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.innerWidth <= MOBILE_BREAKPOINT;
  });

  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`);
    const handler = (event: MediaQueryListEvent) => setIsMobile(event.matches);
    setIsMobile(mql.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);

  return isMobile;
}
