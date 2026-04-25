import { useMemo } from "react";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSnapshotContent } from "./hooks";

interface SnapshotContentDrawerProps {
  filename: string | null;
  onOpenChange: (open: boolean) => void;
}

/**
 * Renders the content of a single snapshot in a Vaul drawer. The
 * snapshot body is a JSON object mapping config paths to file
 * content; we render it as plain monospaced text in a `<pre>`
 * (no syntax-highlight library — bundle budget). The "Copy all"
 * button copies the same plain text to the clipboard.
 */
export function SnapshotContentDrawer({
  filename,
  onOpenChange,
}: SnapshotContentDrawerProps) {
  const open = filename !== null;
  const content = useSnapshotContent(filename ?? undefined);

  const text = useMemo(() => {
    const data = content.data;
    if (!data) return "";
    if (!data.snapshot || typeof data.snapshot !== "object") return "";
    // Render as a flat "===== path =====\n<body>" block per file so
    // the drawer is greppable / copy-paste-friendly.
    return Object.entries(data.snapshot)
      .map(([path, body]) => `===== ${path} =====\n${body}`)
      .join("\n\n");
  }, [content.data]);

  const handleCopy = () => {
    if (!text) return;
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard
        .writeText(text)
        .then(() => toast.success("Snapshot copied to clipboard"))
        .catch(() => toast.error("Copy failed"));
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className="max-h-[80vh]"
        data-testid="snapshot-content-drawer"
      >
        <SheetHeader>
          <SheetTitle>{filename ?? "Snapshot"}</SheetTitle>
          <SheetDescription>
            Plain-text dump of every config file captured at this point.
            API keys are redacted by the controller.
          </SheetDescription>
        </SheetHeader>
        <div className="flex items-center justify-end gap-2 px-4">
          <Button
            variant="secondary"
            size="sm"
            onClick={handleCopy}
            disabled={!text || content.isLoading}
            data-testid="snapshot-copy-all"
          >
            <Copy aria-hidden />
            Copy all
          </Button>
        </div>
        <div className="overflow-auto p-4">
          {content.isLoading ? (
            <div className="space-y-2" data-testid="snapshot-content-loading">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-4 w-full" />
              ))}
            </div>
          ) : content.error ? (
            <div
              role="alert"
              data-testid="snapshot-content-error"
              className="text-sm text-danger"
            >
              {content.error.message}
            </div>
          ) : (
            <pre
              className="max-h-[60vh] overflow-auto whitespace-pre rounded-md border border-border bg-bg-2 p-3 font-mono text-xs leading-relaxed text-fg"
              data-testid="snapshot-content-pre"
            >
              {text}
            </pre>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
