import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

// `@vitejs/plugin-react` ships Vite-6 plugin types, but vitest@2.1 still
// peers against Vite-5 types, so the structural check fails at compile
// time. The runtime contract is identical; drop through `unknown` to
// bridge. Remove once vitest@3.x (native Vite-6 peer) lands.
const reactPlugin = react() as unknown as never;

export default defineConfig({
  plugins: [reactPlugin],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // The vite-plugin-pwa virtual module isn't available during
      // `vitest` runs (the plugin only emits it at build time). Alias
      // it to a tiny stub so any module that imports it doesn't fail
      // resolution; tests that need real PWA behavior install their
      // own mock via vi.mock("virtual:pwa-register").
      "virtual:pwa-register": path.resolve(
        __dirname, "./src/test/pwa-virtual-stub.ts",
      ),
    },
  },
  test: {
    environment: "happy-dom",
    setupFiles: ["./src/test/setup.ts"],
    // Playwright e2e specs live under tests/e2e and use a different
    // runner; keep them out of the vitest pool.
    exclude: ["node_modules/**", "tests/e2e/**", "dist/**"],
    coverage: {
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.stories.tsx",
        "src/**/*.test.{ts,tsx}",
        // Generated types — no logic to cover.
        "src/api/types.ts",
        // Entrypoints + routing wiring — exercised by Playwright e2e,
        // not unit tests. Including them would inflate the coverage
        // denominator with bind-only code that has no meaningful
        // branches.
        "src/main.tsx",
        "src/App.tsx",
        "src/routeTree.ts",
        "src/routes/**",
        // Vite ambient declarations.
        "src/vite-env.d.ts",
        // Test helpers.
        "src/test/**",
      ],
      reporter: ["text", "html"],
      thresholds: {
        lines: 85,
        branches: 75,
        functions: 85,
        statements: 85,
      },
    },
  },
});
