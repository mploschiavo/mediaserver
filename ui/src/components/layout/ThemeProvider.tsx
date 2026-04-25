import {
  ThemeProvider as NextThemesProvider,
  useTheme as useNextTheme,
} from "next-themes";
import { useEffect, type ReactNode } from "react";

interface ThemeProviderProps {
  children: ReactNode;
}

/**
 * `next-themes` wrapper that mirrors the resolved theme onto
 * `document.documentElement.dataset.theme` so our OKLCH design
 * tokens (defined per `[data-theme=...]` block) resolve. The
 * `index.html` inline script writes the initial value before
 * hydration to avoid FOUC; this provider continues from there.
 */
export function ThemeProvider({ children }: ThemeProviderProps) {
  return (
    <NextThemesProvider
      attribute="data-theme"
      defaultTheme="system"
      enableSystem
      storageKey="theme"
      disableTransitionOnChange
    >
      <ThemeAttributeSync />
      {children}
    </NextThemesProvider>
  );
}

function ThemeAttributeSync() {
  const { resolvedTheme } = useNextTheme();
  useEffect(() => {
    if (!resolvedTheme) return;
    document.documentElement.dataset.theme = resolvedTheme;
  }, [resolvedTheme]);
  return null;
}

export { useNextTheme as useTheme };
