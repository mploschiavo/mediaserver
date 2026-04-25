// Vitest entrypoint auto-extends Vitest's `expect` (not jest's).
// The bare "@testing-library/jest-dom" import calls `expect.extend(...)` at
// module load — Vitest's `expect` is per-test-context and not in scope, so
// the bare import throws `ReferenceError: expect is not defined`.
import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Vitest 2.x does NOT auto-unmount Testing Library renders between
// tests. Without this, every test in `it.each` accumulates DOM, which
// trips "Found multiple elements" in the next iteration.
afterEach(() => {
  cleanup();
});

// Mock the Vite PWA plugin's virtual module globally. The module
// only exists at build time when vite-plugin-pwa is in the pipeline;
// vitest doesn't see it, so any test that transitively imports
// src/lib/pwa.ts would otherwise fail with "Failed to resolve
// import 'virtual:pwa-register'". Tests that need real PWA behavior
// can override per-file with vi.mock(...).
vi.mock("virtual:pwa-register", () => ({
  registerSW: vi.fn(() => vi.fn()),
}));

// happy-dom polyfills missing in 15.x. Radix UI primitives
// (Select, Dialog, DropdownMenu) call these on the underlying
// element in React 19's pointer-event model; without the polyfill
// every component that uses Pointer Capture throws
// "TypeError: target.hasPointerCapture is not a function" at first
// click. Stubs are sufficient — happy-dom doesn't simulate the
// underlying capture state and the components only branch on
// "is the call available?".
if (typeof Element !== "undefined") {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = function (): boolean {
      return false;
    };
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = function (): void {};
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = function (): void {};
  }
  // Radix Select also calls scrollIntoView when an option is
  // highlighted; happy-dom stubs but some versions return undefined.
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function (): void {};
  }
}
