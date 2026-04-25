import { useCallback } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { toast } from "sonner";
import { ApiError, useEnforceConfig } from "@/api";
import { Button } from "@/components/ui/button";

interface EnforceButtonProps {
  disabled?: boolean;
}

/**
 * Secondary-CTA button. Pushes the controller's policy snapshot
 * to every Servarr/Bazarr adapter, flipping any drifted fields.
 * The success toast surfaces the change count when the report
 * provides one; otherwise it falls back to a calm "compliant".
 */
export function EnforceButton({ disabled = false }: EnforceButtonProps) {
  const enforce = useEnforceConfig();

  const handleFire = useCallback(() => {
    if (disabled || enforce.isPending) return;
    enforce.mutate(undefined, {
      onSuccess: (report) => {
        let changes = 0;
        if (report && typeof report === "object") {
          const raw = (report as Record<string, unknown>).changes;
          if (typeof raw === "number") changes = raw;
        }
        toast.success(
          changes > 0
            ? `Enforced — ${changes} field${changes === 1 ? "" : "s"} flipped`
            : "Everything compliant",
        );
      },
      onError: (err) => {
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Enforce failed";
        toast.error(msg);
      },
    });
  }, [disabled, enforce]);

  useHotkeys("e", handleFire, { enabled: !disabled });

  return (
    <Button
      variant="secondary"
      disabled={disabled || enforce.isPending}
      loading={enforce.isPending}
      onClick={handleFire}
      data-testid="enforce-button"
    >
      Enforce config
    </Button>
  );
}
