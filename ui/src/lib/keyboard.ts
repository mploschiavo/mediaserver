/**
 * Keyboard shortcut helpers. The atomic <Kbd> component is owned by
 * the design-system layer and lives in `@/components/ui/kbd`; we
 * re-export it here so layout consumers can grab kbd glyphs and
 * `formatShortcut` from a single import path.
 */
export { Kbd } from "@/components/ui/kbd";

const isMac = (): boolean => {
  if (typeof navigator === "undefined") return false;
  // navigator.userAgentData.platform is the modern surface; fall
  // back to the legacy `platform` string when it isn't present.
  const platform =
    (navigator as Navigator & { userAgentData?: { platform?: string } })
      .userAgentData?.platform ??
    navigator.platform ??
    "";
  return /mac|iphone|ipad|ipod/i.test(platform);
};

const KEY_GLYPH: Record<string, string> = {
  mod: "⌘", // ⌘ on mac, swapped to Ctrl below on other platforms
  cmd: "⌘",
  meta: "⌘",
  ctrl: "Ctrl",
  control: "Ctrl",
  shift: "⇧",
  alt: "⌥",
  option: "⌥",
  enter: "⏎",
  return: "⏎",
  esc: "Esc",
  escape: "Esc",
  tab: "⇥",
  up: "↑",
  down: "↓",
  left: "←",
  right: "→",
  space: "␣",
  backspace: "⌫",
  delete: "⌦",
};

/**
 * Format a shortcut string like "mod+k" or "g m" for display in a
 * <Kbd>. On macOS "mod" renders as ⌘, elsewhere as Ctrl. Sequence
 * shortcuts (separated by spaces) keep their spaces; combo keys
 * (separated by `+`) are joined visually.
 */
export function formatShortcut(shortcut: string): string {
  const mac = isMac();
  const formatToken = (raw: string): string => {
    const key = raw.toLowerCase();
    if (key === "mod") return mac ? KEY_GLYPH.mod! : KEY_GLYPH.ctrl!;
    if (KEY_GLYPH[key]) return KEY_GLYPH[key]!;
    return raw.length === 1 ? raw.toUpperCase() : raw;
  };
  return shortcut
    .split(" ")
    .map((segment) =>
      segment
        .split("+")
        .map(formatToken)
        .join(mac ? "" : "+"),
    )
    .join(" ");
}

/** Resolve the platform-correct binding string for react-hotkeys-hook. */
export function platformShortcut(shortcut: string): string {
  return shortcut.replace(/\bmod\b/g, isMac() ? "meta" : "ctrl");
}
