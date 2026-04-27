import { createRootRoute, Outlet } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { EventStreamProvider } from "@/lib/events/EventStreamProvider";

export const Route = createRootRoute({
  component: () => (
    <EventStreamProvider>
      <AppShell>
        <Outlet />
      </AppShell>
    </EventStreamProvider>
  ),
});
