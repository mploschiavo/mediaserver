import { useEffect, useState } from "react";
import { animate, useMotionValue, useReducedMotion } from "framer-motion";

/**
 * Tween a number from its previous → next value over `durationSec`.
 * The display value is stringified via `format` so the consumer
 * renders it directly (no second formatting pass needed). Honors
 * `prefers-reduced-motion`: when the OS toggle is on, the hook
 * snaps to the target instead of animating.
 *
 * Uses Framer Motion's `animate(...)` helper rather than a manual
 * rAF loop so the easing curve matches every other transition in
 * the design system (`--ease-out` = cubic-bezier(.16,1,.3,1)).
 */
export function useBytesCounter(
  target: number,
  format: (n: number) => string,
  durationSec = 1.2,
): string {
  const mv = useMotionValue(target);
  const [display, setDisplay] = useState(() => format(target));
  const reduce = useReducedMotion();

  useEffect(() => {
    if (reduce) {
      mv.set(target);
      setDisplay(format(target));
      return;
    }
    const controls = animate(mv, target, {
      duration: durationSec,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (n) => setDisplay(format(n)),
    });
    return () => controls.stop();
  }, [target, mv, format, durationSec, reduce]);

  return display;
}
