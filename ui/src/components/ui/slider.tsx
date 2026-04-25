import * as React from "react";
import { cn } from "@/lib/cn";

/**
 * Lightweight slider primitive — a styled `<input type="range">`.
 *
 * The wave-1 dashboard didn't ship `@radix-ui/react-slider`, so this
 * wrapper keeps the bundle parsimonious by leaning on the native
 * range input and Tailwind's pseudo-element selectors. The visual
 * language (track tinted with `bg-3`, accent thumb, focus ring) is
 * deliberately aligned with the shadcn Switch for consistency.
 *
 * The thumb is a 20px disc — well above the 24px hit-target floor in
 * iOS HIG and inside the 44×44 touch target of the surrounding row,
 * so the control is finger-reachable without overflowing the layout.
 */
export type SliderProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "type" | "onChange"
> & {
  onValueChange?: (value: number) => void;
};

const Slider = React.forwardRef<HTMLInputElement, SliderProps>(
  ({ className, onValueChange, ...props }, ref) => (
    <input
      ref={ref}
      type="range"
      className={cn(
        // Track + base layout
        "h-2 w-full cursor-pointer appearance-none rounded-full",
        "bg-bg-3",
        // WebKit thumb
        "[&::-webkit-slider-thumb]:appearance-none",
        "[&::-webkit-slider-thumb]:h-5 [&::-webkit-slider-thumb]:w-5",
        "[&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent",
        "[&::-webkit-slider-thumb]:shadow-md",
        "[&::-webkit-slider-thumb]:transition-transform",
        "[&::-webkit-slider-thumb]:duration-[var(--duration-base)]",
        "[&::-webkit-slider-thumb]:ease-[var(--ease-out)]",
        "[&::-webkit-slider-thumb]:hover:scale-110",
        // Firefox thumb
        "[&::-moz-range-thumb]:h-5 [&::-moz-range-thumb]:w-5",
        "[&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0",
        "[&::-moz-range-thumb]:bg-accent [&::-moz-range-thumb]:shadow-md",
        // Focus ring lifted from the surrounding focus-visible style
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      onChange={(ev) => {
        const n = Number(ev.target.value);
        if (Number.isFinite(n)) onValueChange?.(n);
      }}
      {...props}
    />
  ),
);
Slider.displayName = "Slider";

export { Slider };
