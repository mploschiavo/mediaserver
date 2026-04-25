import { useState } from "react";
import { Bell, Info, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

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
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { asArray } from "@/lib/coerce";

import {
  newAlertRuleId,
  useAlertRules,
  type AlertRule,
} from "./hooks";

const CONDITION_OPTIONS: ReadonlyArray<{
  value: AlertRule["condition"];
  label: string;
}> = [
  { value: "down", label: "Down" },
  { value: "degraded", label: "Degraded" },
  { value: "any", label: "Any non-OK" },
];

interface AddRuleDialogProps {
  onAdd: (rule: AlertRule) => void;
}

function AddRuleDialog({ onAdd }: AddRuleDialogProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [service, setService] = useState("*");
  const [condition, setCondition] = useState<AlertRule["condition"]>("down");
  const [threshold, setThreshold] = useState("3");

  const reset = () => {
    setName("");
    setService("*");
    setCondition("down");
    setThreshold("3");
  };

  const submit = () => {
    const trimmed = name.trim();
    const targetService = service.trim() || "*";
    const parsed = Number.parseInt(threshold, 10);
    if (!trimmed) {
      toast.error("Rule name is required");
      return;
    }
    if (!Number.isFinite(parsed) || parsed < 1) {
      toast.error("Threshold must be at least 1");
      return;
    }
    onAdd({
      id: newAlertRuleId(),
      name: trimmed,
      service: targetService,
      condition,
      threshold: parsed,
      action: "toast",
    });
    reset();
    setOpen(false);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        setOpen(next);
      }}
    >
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="primary"
          size="sm"
          data-testid="alert-rules-add-trigger"
        >
          <Plus aria-hidden className="size-3.5" />
          Add rule
        </Button>
      </DialogTrigger>
      <DialogContent data-testid="alert-rules-add-dialog">
        <DialogHeader>
          <DialogTitle>Add alert rule</DialogTitle>
          <DialogDescription>
            Rules evaluate against the live `/api/health` snapshot every 30
            seconds in this browser.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="alert-rule-name">Name</Label>
            <Input
              id="alert-rule-name"
              data-testid="alert-rule-name"
              value={name}
              placeholder="Sonarr down for 3 checks"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="alert-rule-service">Target service</Label>
            <Input
              id="alert-rule-service"
              data-testid="alert-rule-service"
              value={service}
              placeholder="* matches any"
              onChange={(e) => setService(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="alert-rule-condition">Condition</Label>
            <Select
              value={condition}
              onValueChange={(v) =>
                setCondition(v as AlertRule["condition"])
              }
            >
              <SelectTrigger
                id="alert-rule-condition"
                data-testid="alert-rule-condition"
              >
                <SelectValue placeholder="Pick a condition" />
              </SelectTrigger>
              <SelectContent>
                {CONDITION_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="alert-rule-threshold">
              Threshold (consecutive checks)
            </Label>
            <Input
              id="alert-rule-threshold"
              data-testid="alert-rule-threshold"
              type="number"
              min={1}
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="secondary"
            onClick={() => setOpen(false)}
            data-testid="alert-rule-cancel"
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="primary"
            onClick={submit}
            data-testid="alert-rule-submit"
          >
            Save rule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Alert rules card. Mirrors the dashboard.html legacy alert table
 * (`dashboard.html:691`) — name, target service, condition,
 * threshold, action — but stored in `localStorage` because the
 * controller's OpenAPI surface still has no alert-rule endpoint.
 *
 * The disclaimer banner makes the client-side scope explicit so an
 * operator never assumes their rules are mirrored to the server.
 */
export function AlertRulesCard() {
  const { rules, save, remove } = useAlertRules();
  // Defensive coercion: even though the hook returns a typed
  // readonly array, downstream tables consume `asArray()` so a
  // stale snapshot from another tab can never crash the view.
  // ResponsiveTable wants a mutable array, so spread the readonly view.
  const rows: AlertRule[] = [...asArray<AlertRule>(rules)];

  const columns: ResponsiveTableColumn<AlertRule>[] = [
    {
      id: "name",
      header: "Name",
      cell: (row) => (
        <span className="font-medium text-fg">{row.name}</span>
      ),
    },
    {
      id: "service",
      header: "Service",
      cell: (row) => (
        <span className="font-mono text-xs text-fg-muted">
          {row.service === "*" ? "any" : row.service}
        </span>
      ),
    },
    {
      id: "condition",
      header: "Condition",
      cell: (row) => <Badge variant="default">{row.condition}</Badge>,
    },
    {
      id: "threshold",
      header: "Threshold",
      cell: (row) => (
        <span className="tabular-nums text-fg-muted">
          {row.threshold} {row.threshold === 1 ? "check" : "checks"}
        </span>
      ),
    },
    {
      id: "action",
      header: "Action",
      cell: (row) => <Badge variant="info">{row.action}</Badge>,
    },
    {
      id: "delete",
      header: "",
      cell: (row) => (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => remove(row.id)}
          data-testid={`alert-rule-delete-${row.id}`}
          aria-label={`Delete rule ${row.name}`}
        >
          <Trash2 aria-hidden className="size-3.5" />
          Delete
        </Button>
      ),
    },
  ];

  return (
    <Card data-testid="alert-rules-card">
      <CardHeader className="flex-row items-start justify-between gap-3 sm:items-center">
        <div className="flex flex-col gap-1.5">
          <CardTitle>Alert rules</CardTitle>
          <CardDescription>
            Fire a browser toast when a service trips a rule.
          </CardDescription>
        </div>
        <AddRuleDialog onAdd={save} />
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div
          role="status"
          data-testid="alert-rules-disclaimer"
          className="flex items-start gap-2 rounded-md border border-border bg-bg-1 p-3 text-xs text-fg-muted"
        >
          <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-info" />
          <span>
            These rules run in your browser only — they are stored in
            <code className="mx-1 rounded bg-bg-2 px-1 py-0.5 font-mono text-[0.7rem]">
              localStorage
            </code>
            and are not synced to the server.
          </span>
        </div>
        {rows.length === 0 ? (
          <EmptyState
            icon={Bell}
            title="No alert rules yet"
            description="Add a rule to get a browser toast the moment a service trips its threshold."
          />
        ) : (
          <ResponsiveTable
            rows={rows}
            rowKey={(r) => r.id}
            columns={columns}
            card={(row) => (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-fg">{row.name}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => remove(row.id)}
                    data-testid={`alert-rule-delete-${row.id}-mobile`}
                    aria-label={`Delete rule ${row.name}`}
                  >
                    <Trash2 aria-hidden className="size-3.5" />
                  </Button>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <span className="text-fg-muted">Service</span>
                  <span className="text-right font-mono">
                    {row.service === "*" ? "any" : row.service}
                  </span>
                  <span className="text-fg-muted">Condition</span>
                  <span className="text-right">{row.condition}</span>
                  <span className="text-fg-muted">Threshold</span>
                  <span className="text-right tabular-nums">
                    {row.threshold}
                  </span>
                  <span className="text-fg-muted">Action</span>
                  <span className="text-right">{row.action}</span>
                </div>
              </div>
            )}
          />
        )}
      </CardContent>
    </Card>
  );
}
