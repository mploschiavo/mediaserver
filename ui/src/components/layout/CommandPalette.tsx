import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowRightLeft,
  FileText,
  Layers,
  Moon,
  PlayCircle,
  Route as RouteIcon,
  ScanSearch,
  Settings,
  ShieldCheck,
  Sun,
  TestTube2,
  UserCircle2,
  Users,
  Webhook,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { Kbd, formatShortcut } from "@/lib/keyboard";
import { useTheme } from "@/components/layout/ThemeProvider";
import { cn } from "@/lib/cn";

type CommandKind = "nav" | "action";

interface CommandEntry {
  id: string;
  kind: CommandKind;
  group: "Navigation" | "Actions" | "Recent";
  label: string;
  icon: LucideIcon;
  shortcut?: string;
  keywords?: string[];
  perform: (ctx: PerformCtx) => void | Promise<void>;
}

interface PerformCtx {
  navigate: ReturnType<typeof useNavigate>;
  setOpen: (open: boolean) => void;
  fireAction: (action: AdminAction) => void;
  setTheme: (theme: string) => void;
  resolvedTheme: string | undefined;
}

type AdminAction =
  | "reconcile"
  | "reconcile-dry-run"
  | "enforce-config"
  | "open-audit-log";

const RECENT_KEY = "command-palette:recent";
const RECENT_LIMIT = 5;

const NAV_COMMANDS: Omit<CommandEntry, "perform">[] = [
  {
    id: "nav:content",
    kind: "nav",
    group: "Navigation",
    label: "Go to Content",
    icon: Layers,
    shortcut: "g c",
    keywords: ["library", "media"],
  },
  {
    id: "nav:logs",
    kind: "nav",
    group: "Navigation",
    label: "Go to Logs",
    icon: FileText,
    shortcut: "g l",
    keywords: ["events", "history"],
  },
  {
    id: "nav:routing",
    kind: "nav",
    group: "Navigation",
    label: "Go to Routing",
    icon: RouteIcon,
    shortcut: "g r",
    keywords: ["paths", "rules"],
  },
  {
    id: "nav:ops",
    kind: "nav",
    group: "Navigation",
    label: "Go to Ops",
    icon: Wrench,
    shortcut: "g o",
    keywords: ["operations", "admin"],
  },
  {
    id: "nav:webhooks",
    kind: "nav",
    group: "Navigation",
    label: "Go to Webhooks",
    icon: Webhook,
    shortcut: "g w",
    keywords: ["incoming", "events"],
  },
  {
    id: "nav:users",
    kind: "nav",
    group: "Navigation",
    label: "Go to Users",
    icon: Users,
    shortcut: "g u",
    keywords: ["accounts"],
  },
  {
    id: "nav:me",
    kind: "nav",
    group: "Navigation",
    label: "Go to Me",
    icon: UserCircle2,
    shortcut: "g a",
    keywords: ["account", "profile"],
  },
  {
    id: "nav:media-integrity",
    kind: "nav",
    group: "Navigation",
    label: "Go to Media Integrity",
    icon: ShieldCheck,
    shortcut: "g m",
    keywords: ["audit", "corruption"],
  },
  {
    id: "nav:profile",
    kind: "nav",
    group: "Navigation",
    label: "Go to Profile",
    icon: ScanSearch,
    shortcut: "g p",
    keywords: ["encoder", "ingest"],
  },
  {
    id: "nav:settings",
    kind: "nav",
    group: "Navigation",
    label: "Go to Settings",
    icon: Settings,
    keywords: ["preferences"],
  },
];

const NAV_PATHS: Record<string, string> = {
  "nav:content": "/content",
  "nav:logs": "/logs",
  "nav:routing": "/routing",
  "nav:ops": "/ops",
  "nav:webhooks": "/webhooks",
  "nav:users": "/users",
  "nav:me": "/me",
  "nav:media-integrity": "/media-integrity",
  "nav:profile": "/profile",
  "nav:settings": "/settings",
};

const ACTION_COMMANDS: Omit<CommandEntry, "perform">[] = [
  {
    id: "action:reconcile",
    kind: "action",
    group: "Actions",
    label: "Reconcile now",
    icon: PlayCircle,
    keywords: ["sync", "run"],
  },
  {
    id: "action:reconcile-dry-run",
    kind: "action",
    group: "Actions",
    label: "Reconcile (dry run)",
    icon: TestTube2,
    keywords: ["sync", "preview"],
  },
  {
    id: "action:enforce-config",
    kind: "action",
    group: "Actions",
    label: "Enforce config now",
    icon: ArrowRightLeft,
    keywords: ["apply", "drift"],
  },
  {
    id: "action:open-audit-log",
    kind: "action",
    group: "Actions",
    label: "Open audit log",
    icon: FileText,
    keywords: ["history", "audit"],
  },
];

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * cmdk-driven palette opened by ⌘K / Ctrl+K. Houses navigation,
 * admin actions, recent destinations, and theme toggle. Recents
 * persist to localStorage so they survive reloads.
 */
export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate();
  const { setTheme, resolvedTheme } = useTheme();
  const [search, setSearch] = useState("");
  const [recents, setRecents] = useState<string[]>(() => loadRecents());

  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const adminMutation = useMutation({
    mutationFn: async (action: AdminAction) => {
      // The media-integrity routes are the source of truth; an earlier
      // draft mistakenly pointed at `/api/admin/*` (which doesn't
      // exist on the controller).
      const endpoint: Record<AdminAction, string> = {
        reconcile: "/api/media-integrity/reconcile",
        "reconcile-dry-run": "/api/media-integrity/reconcile?dry_run=1",
        "enforce-config": "/api/media-integrity/enforce-config",
        "open-audit-log": "/api/audit-log",
      };
      if (action === "open-audit-log") {
        navigate({ to: "/logs", search: { audit: 1 } as never });
        return;
      }
      const res = await fetch(endpoint[action], {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
      });
      if (!res.ok) {
        throw new Error(`Action failed (${res.status})`);
      }
    },
    onSuccess: (_data, action) => {
      toast.success(actionLabel(action));
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : "Action failed.");
    },
  });

  const fireAction = useCallback(
    (action: AdminAction) => {
      adminMutation.mutate(action);
    },
    [adminMutation],
  );

  const ctx = useMemo<PerformCtx>(
    () => ({
      navigate,
      setOpen: onOpenChange,
      fireAction,
      setTheme,
      resolvedTheme,
    }),
    [navigate, onOpenChange, fireAction, setTheme, resolvedTheme],
  );

  const navEntries: CommandEntry[] = NAV_COMMANDS.map((entry) => ({
    ...entry,
    perform: ({ navigate: nav, setOpen }) => {
      const path = NAV_PATHS[entry.id];
      if (path) nav({ to: path });
      pushRecent(entry.id, setRecents);
      setOpen(false);
    },
  }));

  const actionEntries: CommandEntry[] = ACTION_COMMANDS.map((entry) => ({
    ...entry,
    perform: ({ setOpen, fireAction: run }) => {
      const map: Record<string, AdminAction> = {
        "action:reconcile": "reconcile",
        "action:reconcile-dry-run": "reconcile-dry-run",
        "action:enforce-config": "enforce-config",
        "action:open-audit-log": "open-audit-log",
      };
      const a = map[entry.id];
      if (a) run(a);
      setOpen(false);
    },
  }));

  const themeEntry: CommandEntry = {
    id: "action:toggle-theme",
    kind: "action",
    group: "Actions",
    label:
      resolvedTheme === "dark"
        ? "Switch to light theme"
        : "Switch to dark theme",
    icon: resolvedTheme === "dark" ? Sun : Moon,
    keywords: ["dark", "light", "theme"],
    perform: ({ setTheme: set, setOpen, resolvedTheme: rt }) => {
      set(rt === "dark" ? "light" : "dark");
      setOpen(false);
    },
  };

  const allEntries: CommandEntry[] = [
    ...navEntries,
    ...actionEntries,
    themeEntry,
  ];

  const recentEntries: CommandEntry[] = recents
    .map((id) => allEntries.find((entry) => entry.id === id))
    .filter((entry): entry is CommandEntry => Boolean(entry))
    .map((entry) => ({ ...entry, group: "Recent" }));

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open ? (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
              />
            </Dialog.Overlay>
            <Dialog.Content asChild>
              <motion.div
                initial={{ opacity: 0, y: 8, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 4, scale: 0.98 }}
                transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
                className={cn(
                  "fixed left-1/2 top-[18vh] z-50 w-[540px] max-w-[calc(100vw-2rem)] -translate-x-1/2",
                  "rounded-xl border border-border bg-popover/95 text-popover-fg shadow-2xl backdrop-blur-md",
                )}
                role="dialog"
                aria-label="Command palette"
              >
                <Dialog.Title className="sr-only">Command palette</Dialog.Title>
                <Dialog.Description className="sr-only">
                  Search navigation and actions
                </Dialog.Description>
                <Command label="Command palette" className="flex flex-col">
                  <div className="flex items-center gap-2 border-b border-border px-3.5">
                    <Command.Input
                      autoFocus
                      value={search}
                      onValueChange={setSearch}
                      placeholder="Type a command or search…"
                      className="h-12 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-fg-faint"
                    />
                    <Kbd>Esc</Kbd>
                  </div>
                  <Command.List className="max-h-[400px] overflow-y-auto p-2">
                    <Command.Empty className="px-3 py-6 text-center text-sm text-fg-muted">
                      No results.
                    </Command.Empty>

                    {recentEntries.length > 0 && search === "" ? (
                      <CommandGroup heading="Recent">
                        {recentEntries.map((entry) => (
                          <CommandRow
                            key={`recent-${entry.id}`}
                            entry={entry}
                            ctx={ctx}
                          />
                        ))}
                      </CommandGroup>
                    ) : null}

                    <CommandGroup heading="Navigation">
                      {navEntries.map((entry) => (
                        <CommandRow key={entry.id} entry={entry} ctx={ctx} />
                      ))}
                    </CommandGroup>
                    <CommandGroup heading="Actions">
                      {actionEntries.map((entry) => (
                        <CommandRow key={entry.id} entry={entry} ctx={ctx} />
                      ))}
                      <CommandRow entry={themeEntry} ctx={ctx} />
                    </CommandGroup>
                  </Command.List>
                </Command>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        ) : null}
      </AnimatePresence>
    </Dialog.Root>
  );
}

function CommandGroup({
  heading,
  children,
}: {
  heading: string;
  children: React.ReactNode;
}) {
  return (
    <Command.Group
      heading={heading}
      className="mb-2 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-fg-faint"
    >
      {children}
    </Command.Group>
  );
}

function CommandRow({
  entry,
  ctx,
}: {
  entry: CommandEntry;
  ctx: PerformCtx;
}) {
  const Icon = entry.icon;
  return (
    <Command.Item
      value={`${entry.label} ${entry.keywords?.join(" ") ?? ""}`}
      onSelect={() => entry.perform(ctx)}
      className={cn(
        "flex cursor-pointer select-none items-center gap-2.5 rounded-md px-2 py-2 text-sm text-fg outline-none",
        "data-[selected=true]:bg-bg-2",
      )}
    >
      <Icon className="size-4 text-fg-faint" aria-hidden />
      <span className="flex-1 truncate">{entry.label}</span>
      {entry.shortcut ? (
        <Kbd className="text-[10px]">{formatShortcut(entry.shortcut)}</Kbd>
      ) : null}
    </Command.Item>
  );
}

/**
 * Hook that wires global ⌘K / Ctrl+K to open the palette. Returns
 * `[open, setOpen]` so the AppShell can mount the dialog itself.
 */
export function useCommandPalette(): [
  boolean,
  React.Dispatch<React.SetStateAction<boolean>>,
] {
  const [open, setOpen] = useState(false);
  useHotkeys(
    "mod+k",
    (event) => {
      event.preventDefault();
      setOpen((current) => !current);
    },
    { enableOnFormTags: true, preventDefault: true },
    [],
  );
  return [open, setOpen];
}

function actionLabel(action: AdminAction): string {
  switch (action) {
    case "reconcile":
      return "Reconcile started.";
    case "reconcile-dry-run":
      return "Dry-run reconcile started.";
    case "enforce-config":
      return "Config enforcement started.";
    case "open-audit-log":
      return "Opened audit log.";
  }
}

function loadRecents(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === "string");
  } catch {
    return [];
  }
}

function pushRecent(
  id: string,
  setRecents: React.Dispatch<React.SetStateAction<string[]>>,
): void {
  setRecents((current) => {
    const next = [id, ...current.filter((existing) => existing !== id)].slice(
      0,
      RECENT_LIMIT,
    );
    try {
      window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    } catch {
      // localStorage write failures are non-fatal; the list will
      // simply not persist across reloads.
    }
    return next;
  });
}
