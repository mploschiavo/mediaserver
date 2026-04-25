import { asArray } from "@/lib/coerce";
import { useEffect, useMemo, useState } from "react";
import { Pencil, ShieldCheck } from "lucide-react";
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
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { useRoles, useUpdateRole, type AdminRole } from "./hooks";

// Best-effort superset of the permissions the controller knows
// about. Real permissions come back via /api/roles, but we use this
// list when the role record doesn't expose its own catalog.
const COMMON_PERMISSIONS = [
  "users:read",
  "users:write",
  "roles:write",
  "media:read",
  "media:write",
  "ops:read",
  "ops:write",
  "audit:read",
];

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

function rolePerms(r: AdminRole): readonly string[] {
  return r.permissions ?? r.grants ?? [];
}

export function RolesCard() {
  const roles = useRoles();
  const list = asArray(roles.data?.roles);

  return (
    <Card data-testid="roles-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck aria-hidden className="size-4 text-fg-muted" />
          Roles
        </CardTitle>
        <CardDescription>
          Update role grants. Each role bundles a set of permissions.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        {roles.isLoading ? (
          <div className="space-y-2 p-6" data-testid="roles-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : roles.error ? (
          <p
            role="alert"
            className="px-6 py-4 text-sm text-danger"
            data-testid="roles-error"
          >
            {roles.error.message}
          </p>
        ) : list.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={ShieldCheck}
              title="No roles defined"
              description="The controller hasn't reported any roles yet."
            />
          </div>
        ) : (
          <ul className="divide-y divide-border" data-testid="roles-list">
            {list.map((r) => (
              <li
                key={r.slug}
                className="flex flex-col gap-2 px-6 py-4 sm:flex-row sm:items-center sm:justify-between"
                data-testid={`role-row-${r.slug}`}
              >
                <div className="flex flex-col gap-1">
                  <span className="font-medium text-fg">
                    {r.name ?? r.slug}
                  </span>
                  {r.description ? (
                    <span className="text-xs text-fg-muted">{r.description}</span>
                  ) : null}
                  <div className="flex flex-wrap gap-1 pt-1">
                    {rolePerms(r).map((p) => (
                      <Badge key={p} variant="outline" className="text-[10px]">
                        {p}
                      </Badge>
                    ))}
                  </div>
                </div>
                <RoleEditDialog role={r} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function RoleEditDialog({ role }: { role: AdminRole }) {
  const [open, setOpen] = useState(false);
  const update = useUpdateRole();
  const allPerms = useMemo(() => {
    const set = new Set<string>(COMMON_PERMISSIONS);
    rolePerms(role).forEach((p) => set.add(p));
    return Array.from(set).sort();
  }, [role]);
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(rolePerms(role)),
  );

  // Reset selection when re-opening (or when the role's grants
  // change underneath us via a refetch).
  useEffect(() => {
    if (open) setSelected(new Set(rolePerms(role)));
  }, [open, role]);

  const toggle = (perm: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(perm)) next.delete(perm);
      else next.add(perm);
      return next;
    });
  };

  const handleSave = () => {
    update.mutate(
      { role_slug: role.slug, body: { permissions: Array.from(selected) } },
      {
        onSuccess: () => {
          toast.success(`Role ${role.slug} updated`);
          setOpen(false);
        },
        onError: (err) =>
          toast.error(`Save failed: ${explain(err, "request failed")}`),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button
        variant="secondary"
        size="sm"
        onClick={() => setOpen(true)}
        data-testid={`role-edit-${role.slug}`}
      >
        <Pencil aria-hidden /> Edit
      </Button>
      <DialogContent data-testid={`role-edit-dialog-${role.slug}`}>
        <DialogHeader>
          <DialogTitle>Edit {role.name ?? role.slug}</DialogTitle>
          <DialogDescription>
            Toggle the permissions granted to this role.
          </DialogDescription>
        </DialogHeader>
        <fieldset
          className="grid grid-cols-1 gap-2 sm:grid-cols-2"
          data-testid={`role-edit-perms-${role.slug}`}
        >
          {allPerms.map((perm) => (
            <label
              key={perm}
              className="flex cursor-pointer items-center gap-2 rounded-md border border-border bg-bg-1 px-3 py-2 text-sm"
            >
              <input
                type="checkbox"
                checked={selected.has(perm)}
                onChange={() => toggle(perm)}
                data-testid={`role-perm-${role.slug}-${perm}`}
              />
              <span className="font-mono text-xs">{perm}</span>
            </label>
          ))}
        </fieldset>
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="secondary">
              Cancel
            </Button>
          </DialogClose>
          <Button
            type="button"
            variant="primary"
            loading={update.isPending}
            onClick={handleSave}
            data-testid={`role-save-${role.slug}`}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
