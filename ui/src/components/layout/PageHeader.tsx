import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
}

/**
 * Page-level header used inside route components. The title sits
 * left, the actions sit right; description tucks under the title
 * in a softer color. Keeps every tab visually consistent without
 * forcing each route to roll its own heading block.
 */
export function PageHeader({
  title,
  description,
  actions,
  className,
}: PageHeaderProps) {
  return (
    <header
      className={cn(
        "flex flex-col gap-3 border-b border-border pb-6 sm:flex-row sm:items-end sm:justify-between",
        className,
      )}
    >
      <div className="flex flex-col gap-1.5">
        <h1 className="text-2xl font-semibold tracking-tight text-fg">
          {title}
        </h1>
        {description ? (
          <p className="text-sm text-fg-muted">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex items-center gap-2 sm:flex-shrink-0">{actions}</div>
      ) : null}
    </header>
  );
}
