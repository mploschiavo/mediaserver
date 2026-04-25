import { asArray } from "@/lib/coerce";
import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { UserCog, Users as UsersIcon } from "lucide-react";
import { useUsers } from "@/api";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { EmergencyRevokeCard } from "@/features/emergency-revoke/EmergencyRevokeCard";
import { AddUserDialog } from "@/features/users-admin/AddUserDialog";
import { BulkImportDialog } from "@/features/users-admin/BulkImportDialog";
import { InvitesCard } from "@/features/users-admin/InvitesCard";
import { PasswordPolicyCard } from "@/features/users-admin/PasswordPolicyCard";
import { ProviderReconcileCard } from "@/features/users-admin/ProviderReconcileCard";
import { RolesCard } from "@/features/users-admin/RolesCard";
import { UsersTable } from "@/features/users-admin/UsersTable";
import { useRoles } from "@/features/users-admin/hooks";
import { Route as RootRoute } from "@/routes/__root";

function UsersPage() {
  const reduce = useReducedMotion();
  const users = useUsers();
  const roles = useRoles();
  const roleList = asArray(roles.data?.roles);

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Users"
        description="Identity and access across the stack."
        actions={
          <div className="flex items-center gap-2">
            <BulkImportDialog />
            <AddUserDialog roles={roleList} />
          </div>
        }
      />

      <Card data-testid="users-stats">
        <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0 pb-2">
          <CardTitle className="flex items-center gap-2 text-sm font-medium text-fg-muted">
            <UsersIcon aria-hidden className="size-4" />
            Directory
          </CardTitle>
          <UserCog aria-hidden className="size-4 text-fg-muted" />
        </CardHeader>
        <CardContent>
          {users.isLoading ? (
            <Skeleton className="h-6 w-64" />
          ) : (
            <p className="text-sm text-fg">
              <span className="font-mono tabular-nums">
                {users.data?.users.length ?? 0}
              </span>{" "}
              users ·{" "}
              <span className="font-mono tabular-nums">
                {users.data?.admins ?? 0}
              </span>{" "}
              admins ·{" "}
              <span className="font-mono tabular-nums">
                {users.data?.pending_invites ?? 0}
              </span>{" "}
              pending invite{users.data?.pending_invites === 1 ? "" : "s"}
            </p>
          )}
        </CardContent>
      </Card>

      {users.error ? (
        <div
          role="alert"
          data-testid="users-error"
          className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
        >
          <p className="font-medium">Failed to load users</p>
          <p className="mt-1 text-fg-muted">{users.error.message}</p>
          <button
            type="button"
            onClick={() => users.refetch()}
            className="mt-2 rounded-md border border-border px-3 py-1 text-xs"
          >
            Retry
          </button>
        </div>
      ) : null}

      <Tabs defaultValue="members" className="flex flex-col gap-4">
        <TabsList className="self-start" data-testid="users-tablist">
          <TabsTrigger value="members" data-testid="users-tab-members">
            Members
          </TabsTrigger>
          <TabsTrigger value="roles" data-testid="users-tab-roles">
            Roles
          </TabsTrigger>
          <TabsTrigger value="invites" data-testid="users-tab-invites">
            Invites
          </TabsTrigger>
          <TabsTrigger value="policy" data-testid="users-tab-policy">
            Password policy
          </TabsTrigger>
          <TabsTrigger value="providers" data-testid="users-tab-providers">
            Providers
          </TabsTrigger>
        </TabsList>

        <TabsContent value="members">
          <UsersTable roles={roleList} />
        </TabsContent>
        <TabsContent value="roles">
          <RolesCard />
        </TabsContent>
        <TabsContent value="invites">
          <InvitesCard roles={roleList} />
        </TabsContent>
        <TabsContent value="policy">
          <PasswordPolicyCard />
        </TabsContent>
        <TabsContent value="providers">
          <ProviderReconcileCard />
        </TabsContent>
      </Tabs>

      {/* Break-glass: every-session-revoke. Lives at the bottom of
          the security/users surface so it's contextually grouped
          with user admin without distracting from day-to-day work. */}
      <EmergencyRevokeCard />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/users",
  component: UsersPage,
});
