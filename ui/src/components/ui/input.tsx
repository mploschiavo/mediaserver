import * as React from "react";
import { cn } from "@/lib/cn";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type = "text", ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        // text-base on mobile prevents iOS Safari's auto-zoom on focus
        // (it triggers below 16px); shrinks to 14px from sm+.
        "flex h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-base sm:text-sm text-fg shadow-sm transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out)] file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-fg-faint [@media(hover:hover)]:hover:border-border-strong focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export { Input };
