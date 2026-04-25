import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/cn";

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

/**
 * Reusable empty / zero-state block. Used wherever a list, table,
 * or panel has nothing meaningful to show. The icon-circle nods to
 * the Linear / Cal.com pattern: enough visual presence that the
 * blank space looks intentional, not broken.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      className={cn(
        "flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-border bg-bg-1/40 px-6 py-16 text-center",
        className,
      )}
    >
      {Icon ? (
        <div className="flex size-12 items-center justify-center rounded-full bg-bg-2 text-fg-muted">
          <Icon className="size-5" aria-hidden />
        </div>
      ) : null}
      <div className="flex flex-col gap-1">
        <h3 className="text-base font-medium text-fg">{title}</h3>
        {description ? (
          <p className="max-w-sm text-sm text-fg-muted">{description}</p>
        ) : null}
      </div>
      {action ? <div className="mt-2">{action}</div> : null}
    </motion.div>
  );
}
