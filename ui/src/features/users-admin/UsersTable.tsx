import { asArray } from "@/lib/coerce";
import { useState } from "react";
import {
  History,
  KeyRound,
  MoreHorizontal,
  Pencil,
  Trash2,
  Users as UsersIcon,
  UserX,
} from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { formatRelative } from "@/features/media-integrity/format";
import {
  useDeleteUser,
  useResetUserPassword,
  useRevokeUserSessions,
  useSetUserRole,
  useSetUserState,
  useUsersAdmin,
  type AdminRole,
  type AdminUser,
} from "./hooks";
import { UserDetailDrawer } from "./UserDetailDrawer";

interface UsersTableProps {
  /** Roles surface — shown in the inline role-change dropdown. */
  roles?: readonly AdminRole[];
}

function avatarInitials(name?: string): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const second = parts[1]?.[0] ?? "";
  return (first + second).toUpperCase() || name[0]?.toUpperCase() || "?";
}

function roleVariant(role?: string): "info" | "default" | "outline" {
  if (role === "admin") return "info";
  if (role === "operator") return "default";
  return "outline";
}

function statusVariant(
  status?: string,
): "success" | "warning" | "danger" | "default" {
  if (status === "active") return "success";
  if (status === "disabled") return "warning";
  if (status === "locked" || status === "deleted") return "danger";
  return "default";
}

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

function userRole(u: AdminUser): string {
  return (u.role_slug ?? u.role ?? "viewer") as string;
}

function userStatus(u: AdminUser): string {
  return (u.status ?? u.state ?? "active") as string;
}

function userIdentity(u: AdminUser): string {
  return u.display_name ?? u.username ?? u.email ?? u.id;
}

interface RowActionsProps {
  user: AdminUser;
  onResetPassword: (u: AdminUser) => void;
  onRevokeSessions: (u: AdminUser) => void;
  onViewHistory: (u: AdminUser) => void;
  onEdit: (u: AdminUser) => void;
  onDelete: (u: AdminUser) => void;
}

function RowActions({
  user,
  onResetPassword,
  onRevokeSessions,
  onViewHistory,
  onEdit,
  onDelete,
}: RowActionsProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Actions for ${userIdentity(user)}`}
          data-testid={`user-actions-${user.id}`}
        >
          <MoreHorizontal aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Manage</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => onResetPassword(user)}
          data-testid={`user-action-reset-${user.id}`}
        >
          <KeyRound aria-hidden /> Reset password
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => onRevokeSessions(user)}
          data-testid={`user-action-revoke-${user.id}`}
        >
          <UserX aria-hidden /> Revoke sessions
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => onViewHistory(user)}
          data-testid={`user-action-history-${user.id}`}
        >
          <History aria-hidden /> View login history
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => onEdit(user)}
          data-testid={`user-action-edit-${user.id}`}
        >
          <Pencil aria-hidden /> Edit
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => onDelete(user)}
          data-testid={`user-action-delete-${user.id}`}
          className="text-danger focus:text-danger"
        >
          <Trash2 aria-hidden /> Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const STATE_OPTIONS: { value: string; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "disabled", label: "Disabled" },
  { value: "locked", label: "Locked" },
];

/**
 * Replaces the stub user table on the /users route. Owns the
 * row-level mutations (role change, state change, reset password,
 * revoke sessions, delete) and surfaces a detail drawer for the
 * full per-user view.
 */
export function UsersTable({ roles = [] }: UsersTableProps) {
  const usersQuery = useUsersAdmin();
  const setRole = useSetUserRole();
  const setState = useSetUserState();
  const resetPassword = useResetUserPassword();
  const revokeSessions = useRevokeUserSessions();
  const deleteUser = useDeleteUser();

  const [drawerUser, setDrawerUser] = useState<AdminUser | null>(null);
  const [drawerTab, setDrawerTab] = useState<
    "profile" | "sessions" | "login-history"
  >("profile");

  const list = asArray(usersQuery.data?.users);

  const roleOptions: { value: string; label: string }[] = roles.length
    ? roles.map((r) => ({
        value: r.slug,
        label: r.name ?? r.slug,
      }))
    : [
        { value: "admin", label: "admin" },
        { value: "operator", label: "operator" },
        { value: "viewer", label: "viewer" },
      ];

  const handleRoleChange = (user: AdminUser, role_slug: string) => {
    setRole.mutate(
      { user_id: user.id, role_slug },
      {
        onSuccess: () => toast.success(`Role updated for ${userIdentity(user)}`),
        onError: (err) =>
          toast.error(`Role change failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const handleStateChange = (user: AdminUser, state: string) => {
    setState.mutate(
      { user_id: user.id, state },
      {
        onSuccess: () =>
          toast.success(`${userIdentity(user)} → ${state}`),
        onError: (err) =>
          toast.error(`State change failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const handleResetPassword = (user: AdminUser) => {
    resetPassword.mutate(
      { user_id: user.id },
      {
        onSuccess: () =>
          toast.success(`Password reset issued for ${userIdentity(user)}`),
        onError: (err) =>
          toast.error(`Reset failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const handleRevokeSessions = (user: AdminUser) => {
    revokeSessions.mutate(
      { user_id: user.id },
      {
        onSuccess: () =>
          toast.success(`All sessions revoked for ${userIdentity(user)}`),
        onError: (err) =>
          toast.error(`Revoke failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const handleDelete = (user: AdminUser) => {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Delete user ${userIdentity(user)}? This is reversible.`)
    ) {
      return;
    }
    deleteUser.mutate(
      { user_id: user.id },
      {
        onSuccess: () => toast.success(`Deleted ${userIdentity(user)}`),
        onError: (err) =>
          toast.error(`Delete failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const openDrawer = (
    user: AdminUser,
    tab: "profile" | "sessions" | "login-history" = "profile",
  ) => {
    setDrawerUser(user);
    setDrawerTab(tab);
  };

  if (usersQuery.isLoading) {
    return (
      <Card className="p-0" data-testid="users-table">
        <div className="space-y-2 p-6" data-testid="users-table-loading">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      </Card>
    );
  }

  if (usersQuery.error) {
    return (
      <Card className="p-0" data-testid="users-table">
        <div
          role="alert"
          data-testid="users-table-error"
          className="px-6 py-6 text-sm text-danger"
        >
          {usersQuery.error.message}
        </div>
      </Card>
    );
  }

  if (list.length === 0) {
    return (
      <Card className="p-6" data-testid="users-table">
        <EmptyState
          icon={UsersIcon}
          title="No users"
          description="Invite a teammate to get started."
        />
      </Card>
    );
  }

  const columns: ResponsiveTableColumn<AdminUser>[] = [
    {
      id: "user",
      header: "User",
      cell: (row) => (
        <button
          type="button"
          onClick={() => openDrawer(row, "profile")}
          className="flex items-center gap-3 text-left"
          data-testid={`user-row-trigger-${row.id}`}
        >
          <Avatar>
            <AvatarFallback>
              {avatarInitials(row.display_name ?? row.username)}
            </AvatarFallback>
          </Avatar>
          <div className="flex flex-col">
            <span className="font-medium text-fg">
              {row.display_name ?? row.username}
            </span>
            {row.email ? (
              <span className="text-xs text-fg-muted">{row.email}</span>
            ) : null}
          </div>
        </button>
      ),
    },
    {
      id: "role",
      header: "Role",
      cell: (row) => (
        <div className="flex items-center gap-2">
          <Badge variant={roleVariant(userRole(row))}>{userRole(row)}</Badge>
          <Select
            value={userRole(row)}
            onValueChange={(v) => handleRoleChange(row, v)}
          >
            <SelectTrigger
              className="h-7 w-28 text-xs"
              aria-label={`Change role for ${userIdentity(row)}`}
              data-testid={`user-role-select-${row.id}`}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {roleOptions.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (row) => (
        <div className="flex items-center gap-2">
          <Badge variant={statusVariant(userStatus(row))}>
            {userStatus(row)}
          </Badge>
          <Select
            value={
              STATE_OPTIONS.some((o) => o.value === userStatus(row))
                ? userStatus(row)
                : "active"
            }
            onValueChange={(v) => handleStateChange(row, v)}
          >
            <SelectTrigger
              className="h-7 w-28 text-xs"
              aria-label={`Change state for ${userIdentity(row)}`}
              data-testid={`user-state-select-${row.id}`}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ),
    },
    {
      id: "created",
      header: "Created",
      cell: (row) => (
        <span className="tabular-nums text-fg-muted">
          {row.created_at ? formatRelative(row.created_at) : "—"}
        </span>
      ),
    },
    {
      id: "last-login",
      header: "Last login",
      cell: (row) => (
        <span className="tabular-nums text-fg-muted">
          {row.last_login_at ? formatRelative(row.last_login_at) : "never"}
        </span>
      ),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      cell: (row) => (
        <RowActions
          user={row}
          onResetPassword={handleResetPassword}
          onRevokeSessions={handleRevokeSessions}
          onViewHistory={(u) => openDrawer(u, "login-history")}
          onEdit={(u) => openDrawer(u, "profile")}
          onDelete={handleDelete}
        />
      ),
    },
  ];

  return (
    <Card className="p-0" data-testid="users-table">
      <ResponsiveTable
        rows={[...list]}
        rowKey={(r) => r.id}
        columns={columns}
        card={(row) => (
          <div className="flex flex-col gap-3" data-testid={`user-card-${row.id}`}>
            <div className="flex items-center justify-between">
              <button
                type="button"
                onClick={() => openDrawer(row, "profile")}
                className="flex items-center gap-2 text-left"
              >
                <Avatar>
                  <AvatarFallback>
                    {avatarInitials(row.display_name ?? row.username)}
                  </AvatarFallback>
                </Avatar>
                <div className="flex flex-col">
                  <span className="font-medium text-fg">
                    {row.display_name ?? row.username}
                  </span>
                  {row.email ? (
                    <span className="text-xs text-fg-muted">{row.email}</span>
                  ) : null}
                </div>
              </button>
              <RowActions
                user={row}
                onResetPassword={handleResetPassword}
                onRevokeSessions={handleRevokeSessions}
                onViewHistory={(u) => openDrawer(u, "login-history")}
                onEdit={(u) => openDrawer(u, "profile")}
                onDelete={handleDelete}
              />
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge variant={roleVariant(userRole(row))}>{userRole(row)}</Badge>
              <Badge variant={statusVariant(userStatus(row))}>
                {userStatus(row)}
              </Badge>
              <span className="ml-auto tabular-nums text-fg-muted">
                {row.last_login_at
                  ? formatRelative(row.last_login_at)
                  : "never"}
              </span>
            </div>
          </div>
        )}
      />

      <UserDetailDrawer
        user={drawerUser}
        initialTab={drawerTab}
        roles={roles}
        onClose={() => setDrawerUser(null)}
      />
    </Card>
  );
}
