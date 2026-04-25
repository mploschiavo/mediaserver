import { Compass } from "lucide-react";
import { Link, createRoute, useRouter } from "@tanstack/react-router";
import { Route as RootRoute } from "@/routes/__root";
import { Button } from "@/components/ui/button";

/**
 * Tanstack Router splat segment ("$") matches anything not claimed
 * by another route — i.e. our 404 surface. Lives at the root level
 * so deep-link typos (e.g. /content/asdf) still hit it.
 */
function NotFound() {
  const router = useRouter();
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col items-center gap-4 px-6 py-24 text-center">
      <div className="flex size-12 items-center justify-center rounded-full bg-bg-2 text-fg-muted">
        <Compass className="size-6" aria-hidden />
      </div>
      <h1 className="text-2xl font-semibold tracking-tight text-fg">
        Lost your way?
      </h1>
      <p className="max-w-sm text-sm text-fg-muted">
        The page you&apos;re looking for doesn&apos;t exist or has moved.
      </p>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <Button variant="primary" asChild>
          <Link to="/">Go home</Link>
        </Button>
        <Button
          variant="secondary"
          onClick={() => {
            router.history.back();
          }}
        >
          Back
        </Button>
      </div>
    </div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "$",
  component: NotFound,
});
