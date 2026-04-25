import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-bg-2 text-fg border border-border [@media(hover:hover)]:hover:bg-bg-3 active:bg-bg-3",
        primary:
          "bg-accent text-accent-fg [@media(hover:hover)]:hover:brightness-110 active:brightness-95 shadow-sm",
        secondary:
          "bg-bg-1 text-fg border border-border [@media(hover:hover)]:hover:bg-bg-2 active:bg-bg-3",
        ghost:
          "bg-transparent text-fg [@media(hover:hover)]:hover:bg-bg-2 active:bg-bg-3",
        danger:
          "bg-danger text-white [@media(hover:hover)]:hover:brightness-110 active:brightness-95 shadow-sm",
        outline:
          "bg-transparent text-fg border border-border-strong [@media(hover:hover)]:hover:bg-bg-2 active:bg-bg-3",
      },
      size: {
        // Mobile-first: 44px floor on touch screens (Apple HIG / Material
        // minimum), shrinks to dense 36px from sm: up where pointer is
        // typically a mouse.
        sm: "h-8 px-3 text-xs",
        md: "h-11 sm:h-9 px-4",
        lg: "h-11 px-6 text-base",
        icon: "size-11 sm:size-9",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant, size, asChild = false, loading = false, disabled, children, ...props },
    ref,
  ) => {
    const Comp = asChild ? Slot : "button";
    // Radix Slot expects EXACTLY one child element; injecting a
    // sibling spinner alongside `children` causes Slot to throw under
    // React 19. When `asChild` we therefore forward the children as-is
    // (callers using asChild typically wrap an anchor/link and don't
    // need the loading spinner anyway). When rendering a real button
    // we keep the spinner-or-children pair and let React handle the
    // null slot.
    if (asChild) {
      return (
        <Comp
          ref={ref}
          className={cn(buttonVariants({ variant, size }), className)}
          disabled={disabled ?? loading}
          data-loading={loading || undefined}
          {...props}
        >
          {children}
        </Comp>
      );
    }
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        disabled={disabled ?? loading}
        data-loading={loading || undefined}
        {...props}
      >
        {loading ? <Loader2 className="animate-spin" aria-hidden /> : null}
        {children}
      </Comp>
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
