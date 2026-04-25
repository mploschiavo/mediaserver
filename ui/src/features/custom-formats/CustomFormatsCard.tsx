import { useState, type FormEvent } from "react";
import { FileJson, Sparkles, Upload } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState } from "@/components/layout/EmptyState";
import { cn } from "@/lib/cn";
import {
  CUSTOM_FORMAT_SERVICES,
  readFormats,
  useCustomFormats,
  useImportCustomFormats,
  type CustomFormatEntry,
  type CustomFormatService,
} from "./hooks";

function explain(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Request failed";
}

const TEXTAREA_CN = cn(
  "flex w-full rounded-md border border-input bg-bg-1 px-3 py-2 text-base sm:text-sm text-fg shadow-sm",
  "transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out)] placeholder:text-fg-faint",
  "focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-bg",
  "disabled:cursor-not-allowed disabled:opacity-50",
  "font-mono",
);

interface ImportDialogProps {
  service: CustomFormatService;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function ImportDialog({ service, open, onOpenChange }: ImportDialogProps) {
  const importer = useImportCustomFormats();
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setContent("");
    setError(null);
  };

  const handleSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    const trimmed = content.trim();
    if (!trimmed) {
      setError("Paste a TRaSH-Guides JSON payload first.");
      return;
    }
    try {
      JSON.parse(trimmed);
    } catch (err) {
      setError(`Invalid JSON: ${(err as Error).message}`);
      return;
    }
    setError(null);
    importer.mutate(
      { service, content: trimmed },
      {
        onSuccess: () => {
          toast.success(`Imported custom formats into ${service}`);
          reset();
          onOpenChange(false);
        },
        onError: (err) => {
          toast.error(`Import failed: ${explain(err)}`);
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent data-testid={`custom-formats-import-dialog-${service}`}>
        <DialogHeader>
          <DialogTitle>Import custom formats</DialogTitle>
          <DialogDescription>
            Paste a TRaSH-Guides-style JSON payload to add it to{" "}
            <span className="font-mono">{service}</span>.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={handleSubmit}
          aria-label={`Import custom formats into ${service}`}
          noValidate
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`custom-formats-import-${service}`}>JSON</Label>
            <textarea
              id={`custom-formats-import-${service}`}
              name="content"
              rows={12}
              value={content}
              onChange={(e) => {
                setContent(e.target.value);
                if (error) setError(null);
              }}
              className={TEXTAREA_CN}
              placeholder='{"trash_id": "...", "name": "...", "specifications": [...]}'
              aria-invalid={error ? "true" : undefined}
              aria-describedby={
                error ? `custom-formats-import-error-${service}` : undefined
              }
              data-testid={`custom-formats-import-textarea-${service}`}
            />
            {error ? (
              <p
                id={`custom-formats-import-error-${service}`}
                role="alert"
                className="text-xs text-danger"
                data-testid={`custom-formats-import-error-${service}`}
              >
                {error}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="secondary">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="submit"
              variant="primary"
              loading={importer.isPending}
              disabled={!content.trim()}
              data-testid={`custom-formats-import-submit-${service}`}
            >
              <Upload aria-hidden />
              Import
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

interface FormatRowProps {
  service: CustomFormatService;
  format: CustomFormatEntry;
}

function FormatRow({ service, format }: FormatRowProps) {
  const id = typeof format.id === "number" ? format.id : undefined;
  const name =
    typeof format.name === "string" && format.name
      ? format.name
      : `Format ${id ?? "?"}`;
  const trash =
    typeof format.trash_id === "string" && format.trash_id
      ? format.trash_id
      : undefined;
  return (
    <li
      className="flex items-center justify-between gap-3 py-2 text-sm"
      data-testid={`custom-format-${service}-${id ?? "unknown"}`}
    >
      <div className="flex flex-col min-w-0 flex-1">
        <span className="truncate font-medium text-fg">{name}</span>
        <div className="flex items-center gap-2 text-xs text-fg-muted">
          {id !== undefined ? (
            <span className="font-mono">id {id}</span>
          ) : null}
          {trash ? (
            <Badge variant="outline" className="font-mono text-[10px]">
              trash {trash}
            </Badge>
          ) : null}
        </div>
      </div>
    </li>
  );
}

interface ServicePanelProps {
  service: CustomFormatService;
}

function ServicePanel({ service }: ServicePanelProps) {
  const query = useCustomFormats(service);
  const [importOpen, setImportOpen] = useState(false);
  const formats = readFormats(query.data);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-end">
        <Button
          variant="primary"
          size="sm"
          onClick={() => setImportOpen(true)}
          data-testid={`custom-formats-import-trigger-${service}`}
        >
          <Upload aria-hidden />
          Import
        </Button>
      </div>
      {query.isLoading ? (
        <div
          className="flex flex-col gap-2"
          data-testid={`custom-formats-loading-${service}`}
        >
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : query.error ? (
        <div
          role="alert"
          data-testid={`custom-formats-error-${service}`}
          className="text-sm text-danger"
        >
          {query.error.message}
        </div>
      ) : formats.length === 0 ? (
        <EmptyState
          icon={FileJson}
          title={`No custom formats for ${service}`}
          description="Import a TRaSH-Guides JSON payload above to get started."
        />
      ) : (
        <ul
          className="divide-y divide-border"
          role="list"
          data-testid={`custom-formats-list-${service}`}
        >
          {formats.map((f, i) => (
            <FormatRow
              key={typeof f.id === "number" ? f.id : `idx-${i}`}
              service={service}
              format={f}
            />
          ))}
        </ul>
      )}
      <ImportDialog
        service={service}
        open={importOpen}
        onOpenChange={setImportOpen}
      />
    </div>
  );
}

export function CustomFormatsCard() {
  return (
    <Card data-testid="custom-formats-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="size-4 text-fg-muted" aria-hidden />
          Custom formats
        </CardTitle>
        <CardDescription>
          Per-service quality custom formats. Import TRaSH-Guides JSON to
          extend the catalogue.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="sonarr">
          <TabsList>
            {CUSTOM_FORMAT_SERVICES.map((s) => (
              <TabsTrigger
                key={s}
                value={s}
                data-testid={`custom-formats-tab-${s}`}
                className="capitalize"
              >
                {s}
              </TabsTrigger>
            ))}
          </TabsList>
          {CUSTOM_FORMAT_SERVICES.map((s) => (
            <TabsContent key={s} value={s} className="mt-3">
              <ServicePanel service={s} />
            </TabsContent>
          ))}
        </Tabs>
      </CardContent>
    </Card>
  );
}
