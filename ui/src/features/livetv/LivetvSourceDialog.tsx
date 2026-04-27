import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Beaker } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { fetcher } from "@/api/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useSaveLivetvSources,
  type LivetvUrlEntry,
} from "./hooks";

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

export type LivetvKind = "tuner" | "guide";

interface ProbeResult {
  ok: boolean;
  status: number;
  content_type: string;
  bytes: number;
  kind: string;
  error: string;
}

interface FormState {
  kind: LivetvKind;
  name: string;
  url: string;
}

interface BaseProps {
  tuners: readonly LivetvUrlEntry[];
  guides: readonly LivetvUrlEntry[];
  trigger: ReactNode;
}

interface AddProps extends BaseProps {
  mode: "add";
}

interface EditProps extends BaseProps {
  mode: "edit";
  /** Original entry being edited (URL is the unique key). */
  entry: LivetvUrlEntry;
  /** Which array the entry lives in. */
  kind: LivetvKind;
}

export type LivetvSourceDialogProps = AddProps | EditProps;

/**
 * Add or edit a single live-TV tuner/guide entry. The URL serves as
 * the unique key in each array; an "edit" that changes the URL is
 * modeled as a delete-old + insert-new round-trip in the same POST
 * body so the backend sees a single canonical replacement.
 */
export function LivetvSourceDialog(props: LivetvSourceDialogProps) {
  const { tuners, guides, trigger } = props;
  const isEdit = props.mode === "edit";
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<FormState>(() =>
    isEdit
      ? { kind: props.kind, name: props.entry.name, url: props.entry.url }
      : { kind: "tuner", name: "", url: "" },
  );
  const save = useSaveLivetvSources();

  // Reset whenever the dialog opens so a stale draft doesn't bleed.
  useEffect(() => {
    if (!open) return;
    if (isEdit) {
      setForm({
        kind: props.kind,
        name: props.entry.name,
        url: props.entry.url,
      });
    } else {
      setForm({ kind: "tuner", name: "", url: "" });
    }
  }, [open, isEdit, props]);

  const probe = useMutation<ProbeResult, Error, string>({
    mutationFn: (url: string) =>
      fetcher("api/livetv-sources/probe", {
        method: "POST",
        body: JSON.stringify({ url }),
      }),
  });

  const handleProbe = () => {
    const url = form.url.trim();
    if (!url) {
      toast.error("URL required to probe");
      return;
    }
    probe.mutate(url, {
      onSuccess: (data) => {
        if (data.ok) {
          toast.success(
            `Probe OK (HTTP ${data.status}, ${data.kind}, ${data.bytes} B sampled)`,
          );
        } else {
          toast.error(
            `Probe failed: ${data.error || "URL does not look like M3U/XMLTV"}`,
          );
        }
      },
      onError: (err) => toast.error(`Probe error: ${explain(err, "")}`),
    });
  };

  const handleSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    const name = form.name.trim();
    const url = form.url.trim();
    if (!name) {
      toast.error("Source name required");
      return;
    }
    if (!url) {
      toast.error("URL required");
      return;
    }

    const target = form.kind === "tuner" ? tuners : guides;
    const next: LivetvUrlEntry = { url, name };

    if (isEdit) {
      // Replace the original entry (matched by old URL) with the new
      // one. If the operator switched kind in the dialog, we delete
      // from the old kind's array and insert into the new one.
      const originalKind = props.kind;
      const originalUrl = props.entry.url;
      const sameKind = originalKind === form.kind;

      // Reject duplicates: a different existing row already uses this URL.
      const dupe = target.some(
        (s) => s.url === url && !(sameKind && s.url === originalUrl),
      );
      if (dupe) {
        toast.error(`URL already used by another source`);
        return;
      }

      const body = sameKind
        ? // Same array: replace in place (preserves order).
          form.kind === "tuner"
          ? {
              tuners: tuners.map((t) =>
                t.url === originalUrl ? next : t,
              ),
            }
          : {
              guides: guides.map((g) =>
                g.url === originalUrl ? next : g,
              ),
            }
        : // Cross-kind move: delete from origin, append to target.
          {
            tuners:
              originalKind === "tuner"
                ? tuners.filter((t) => t.url !== originalUrl)
                : form.kind === "tuner"
                  ? [...tuners, next]
                  : tuners,
            guides:
              originalKind === "guide"
                ? guides.filter((g) => g.url !== originalUrl)
                : form.kind === "guide"
                  ? [...guides, next]
                  : guides,
          };

      save.mutate(body, {
        onSuccess: () => {
          toast.success(`Updated ${name}`);
          setOpen(false);
        },
        onError: (err) =>
          toast.error(`Save failed: ${explain(err, "request failed")}`),
      });
      return;
    }

    if (target.some((s) => s.url === url)) {
      toast.error(`URL already added`);
      return;
    }

    save.mutate(
      form.kind === "tuner"
        ? { tuners: [...tuners, next] }
        : { guides: [...guides, next] },
      {
        onSuccess: () => {
          toast.success(`Added ${name}`);
          setOpen(false);
        },
        onError: (err) =>
          toast.error(`Save failed: ${explain(err, "request failed")}`),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent
        data-testid={isEdit ? "livetv-edit-dialog" : "livetv-add-dialog"}
      >
        <DialogHeader>
          <DialogTitle>
            {isEdit ? "Edit live-TV source" : "Add live-TV source"}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Rename, change the URL, or move between tuner and guide lists. Use Probe to verify the URL responds before saving."
              : "Pick whether you're adding an M3U tuner playlist or an XMLTV EPG guide URL, then provide the URL."}
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={handleSubmit}
          aria-label={isEdit ? "Edit live-TV source" : "Add live-TV source"}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="livetv-kind">Kind</Label>
            <Select
              value={form.kind}
              onValueChange={(v) =>
                setForm((p) => ({ ...p, kind: v as LivetvKind }))
              }
            >
              <SelectTrigger id="livetv-kind" data-testid="livetv-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tuner">Tuner (M3U playlist)</SelectItem>
                <SelectItem value="guide">Guide (XMLTV EPG)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="livetv-name">Source name</Label>
            <Input
              id="livetv-name"
              value={form.name}
              onChange={(e) =>
                setForm((p) => ({ ...p, name: e.target.value }))
              }
              placeholder={
                form.kind === "tuner" ? "My IPTV pack" : "My EPG guide"
              }
              required
              data-testid="livetv-name"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="livetv-url">
              {form.kind === "tuner" ? "M3U playlist URL" : "EPG XMLTV URL"}
            </Label>
            <Input
              id="livetv-url"
              type="url"
              value={form.url}
              onChange={(e) =>
                setForm((p) => ({ ...p, url: e.target.value }))
              }
              placeholder={
                form.kind === "tuner"
                  ? "https://example.com/playlist.m3u"
                  : "https://example.com/epg.xml"
              }
              required
              data-testid="livetv-url"
            />
            <div className="flex items-center justify-between gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleProbe}
                loading={probe.isPending}
                disabled={!form.url.trim() || probe.isPending}
                data-testid="livetv-probe"
              >
                <Beaker aria-hidden className="size-3" /> Probe URL
              </Button>
              {probe.data?.ok ? (
                <span
                  className="text-xs text-success"
                  data-testid="livetv-probe-ok"
                >
                  OK · {probe.data.kind} · {probe.data.bytes} B
                </span>
              ) : probe.data && !probe.data.ok ? (
                <span
                  className="text-xs text-danger"
                  data-testid="livetv-probe-fail"
                >
                  {probe.data.error || "Not M3U/XMLTV"}
                </span>
              ) : null}
            </div>
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
              loading={save.isPending}
              data-testid="livetv-submit"
            >
              {isEdit ? "Save" : "Add"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
