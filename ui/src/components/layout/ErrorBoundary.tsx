import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertOctagon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

interface ErrorBoundaryProps {
  children: ReactNode;
  /**
   * Per-route fallback override. When present, renders instead of
   * the default panel — useful for offering route-specific recovery
   * affordances (e.g. "back to library" on /content).
   */
  fallback?: ReactNode;
  className?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
}

const TRUNCATE_AT = 200;

/**
 * Last-resort error boundary that wraps the app shell. React 19 still
 * needs a class component for `componentDidCatch` / `getDerivedStateFromError`;
 * everything below the catch is functional.
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  override state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surfaces in dev/test consoles so tracebacks don't get swallowed
    // by the user-facing fallback.
     
    console.error("ErrorBoundary caught", error, info);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  private handleCopyDiagnostics = async (): Promise<void> => {
    const { error } = this.state;
    if (!error) return;
    const payload = {
      message: error.message,
      stack: error.stack ?? null,
      route:
        typeof window !== "undefined" ? window.location.pathname : "unknown",
      userAgent:
        typeof navigator !== "undefined" ? navigator.userAgent : "unknown",
      ts: new Date().toISOString(),
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      toast.success("Diagnostics copied to clipboard");
    } catch {
      toast.error("Could not copy diagnostics");
    }
  };

  override render(): ReactNode {
    const { error } = this.state;
    const { children, fallback, className } = this.props;

    if (!error) return children;
    if (fallback !== undefined) return fallback;

    const truncated =
      error.message.length > TRUNCATE_AT
        ? `${error.message.slice(0, TRUNCATE_AT)}…`
        : error.message;

    return (
      <div
        role="alert"
        className={cn(
          "flex min-h-screen w-full items-center justify-center bg-bg p-6 text-fg",
          className,
        )}
      >
        <div className="flex w-full max-w-md flex-col items-center gap-4 rounded-lg border border-border bg-bg-1 p-8 text-center shadow-sm">
          <div className="flex size-12 items-center justify-center rounded-full bg-danger/10 text-danger">
            <AlertOctagon className="size-6" aria-hidden />
          </div>
          <h1 className="text-lg font-semibold">Something broke</h1>
          <p
            className="max-w-sm text-sm text-fg-muted"
            data-testid="error-boundary-message"
          >
            {truncated}
          </p>
          <div className="mt-2 flex w-full flex-col gap-2 sm:flex-row sm:justify-center">
            <Button variant="primary" onClick={this.handleReload}>
              Reload page
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                void this.handleCopyDiagnostics();
              }}
            >
              Copy diagnostics
            </Button>
          </div>
        </div>
      </div>
    );
  }
}
