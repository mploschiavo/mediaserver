import { asArray } from "@/lib/coerce";
import { useState } from "react";
import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PlayCircle, Trash2, Webhook } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import type { WebhookEntryShape } from "@/api";
import { ArrWebhooksCard } from "@/features/webhooks/ArrWebhooksCard";
import {
  useAddWebhook,
  useDeleteWebhook,
  useTestWebhooks,
  useWebhooks,
} from "@/features/webhooks/hooks";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Route as RootRoute } from "@/routes/__root";

const EVENT_TYPES = [
  "movie.imported",
  "tv.episode.imported",
  "track.imported",
  "book.imported",
  "media.deleted",
  "health.degraded",
] as const;

function timeAgo(iso?: string): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "never";
  const m = Math.floor(ms / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

interface WebhookRowProps {
  w: WebhookEntryShape;
  onDelete: (id: string) => void;
  pendingId: string | null;
}

function WebhookRow({ w, onDelete, pendingId }: WebhookRowProps) {
  const isDeleting = pendingId === w.id;
  return (
    <li
      className="flex flex-col gap-2 border-b border-border py-3 last:border-b-0 sm:flex-row sm:items-center sm:justify-between"
      data-testid={`webhook-row-${w.id}`}
    >
      <div className="flex flex-col gap-1.5 min-w-0">
        <span className="truncate font-mono text-xs text-fg">{w.url}</span>
        <div className="flex flex-wrap gap-1">
          {w.events.slice(0, 4).map((e) => (
            <Badge key={e} variant="info">
              {e}
            </Badge>
          ))}
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 sm:justify-end">
        <span className="text-xs text-fg-muted">
          fired {timeAgo(w.last_fired_at)}
        </span>
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Delete webhook ${w.url}`}
          data-testid={`webhook-delete-${w.id}`}
          disabled={isDeleting}
          onClick={() => onDelete(w.id)}
        >
          <Trash2 aria-hidden />
        </Button>
      </div>
    </li>
  );
}

function WebhooksPage() {
  const reduce = useReducedMotion();
  const webhooks = useWebhooks();
  const addWebhook = useAddWebhook();
  const deleteWebhook = useDeleteWebhook();
  const testWebhooks = useTestWebhooks();
  const [url, setUrl] = useState("");
  const [eventType, setEventType] = useState<string | undefined>(undefined);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const list = asArray(webhooks.data?.webhooks);

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault();
    if (!url || !eventType || addWebhook.isPending) return;
    addWebhook.mutate(
      { url, event_type: eventType },
      {
        onSuccess: () => {
          toast.success("Webhook added");
          setUrl("");
          setEventType(undefined);
        },
        onError: (err) => {
          toast.error(errorMessage(err, "Failed to add webhook"));
        },
      },
    );
  };

  const handleDelete = (id: string) => {
    if (deleteWebhook.isPending) return;
    setPendingDeleteId(id);
    deleteWebhook.mutate(
      { id },
      {
        onSuccess: () => {
          toast.success("Webhook removed");
        },
        onError: (err) => {
          toast.error(errorMessage(err, "Failed to remove webhook"));
        },
        onSettled: () => {
          setPendingDeleteId(null);
        },
      },
    );
  };

  const handleTestAll = () => {
    if (testWebhooks.isPending) return;
    testWebhooks.mutate(undefined, {
      onSuccess: (result) => {
        if (result.status === "no_webhooks") {
          toast.info("No webhooks registered yet");
          return;
        }
        const entries = Object.entries(result.results ?? {});
        if (entries.length === 0) {
          toast.success("Test fired");
          return;
        }
        for (const [whUrl, status] of entries) {
          const ok = /^ok\b/i.test(status);
          if (ok) toast.success(`${whUrl}: ${status}`);
          else toast.error(`${whUrl}: ${status}`);
        }
      },
      onError: (err) => {
        toast.error(errorMessage(err, "Test failed"));
      },
    });
  };

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Webhooks"
        description="External services react to media events."
        actions={
          <Button
            variant="secondary"
            onClick={handleTestAll}
            disabled={testWebhooks.isPending}
            loading={testWebhooks.isPending}
            data-testid="webhook-test-all"
          >
            <PlayCircle aria-hidden />
            Test all webhooks
          </Button>
        }
      />

      <Card>
        <CardHeader>
          <CardTitle>Configured webhooks</CardTitle>
          <CardDescription>{list.length} configured</CardDescription>
        </CardHeader>
        <CardContent>
          {webhooks.isLoading ? (
            <div className="space-y-2" data-testid="webhooks-loading">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : webhooks.error ? (
            <div role="alert" data-testid="webhooks-error" className="text-sm text-danger">
              {webhooks.error.message}
            </div>
          ) : list.length === 0 ? (
            <EmptyState
              icon={Webhook}
              title="No webhooks yet"
              description="Webhooks let external services react to media events. Add your first one."
            />
          ) : (
            <ul role="list" data-testid="webhooks-list">
              {list.map((w) => (
                <WebhookRow
                  key={w.id}
                  w={w}
                  onDelete={handleDelete}
                  pendingId={pendingDeleteId}
                />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card data-testid="webhook-add-card">
        <CardHeader>
          <CardTitle>Add webhook</CardTitle>
          <CardDescription>
            Pick a target URL and the event type that triggers it.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_180px_auto] sm:items-end"
            onSubmit={handleAdd}
            data-testid="webhook-add-form"
          >
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="webhook-url">Target URL</Label>
              <Input
                id="webhook-url"
                type="url"
                placeholder="https://example.com/hooks/media"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                data-testid="webhook-url-input"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="webhook-event">Event</Label>
              <Select value={eventType} onValueChange={setEventType}>
                <SelectTrigger
                  id="webhook-event"
                  data-testid="webhook-event-select"
                >
                  <SelectValue placeholder="Select event" />
                </SelectTrigger>
                <SelectContent>
                  {EVENT_TYPES.map((e) => (
                    <SelectItem key={e} value={e}>
                      {e}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              type="submit"
              variant="primary"
              disabled={!url || !eventType || addWebhook.isPending}
              loading={addWebhook.isPending}
              data-testid="webhook-add-submit"
            >
              Add webhook
            </Button>
          </form>
        </CardContent>
      </Card>

      <ArrWebhooksCard />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/webhooks",
  component: WebhooksPage,
});
