import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";

interface SkeletonCardProps {
  className?: string;
}

/**
 * Card-shaped placeholder. Mirrors the heading + 3 body lines pattern
 * we use across most data cards so the layout doesn't shift when the
 * real content lands.
 */
export function SkeletonCard({ className }: SkeletonCardProps) {
  return (
    <Card
      aria-busy="true"
      aria-hidden="true"
      data-testid="skeleton-card"
      className={cn(className)}
    >
      <CardHeader className="gap-2">
        <Skeleton className="h-[60px] w-1/2" />
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-5/6" />
        <Skeleton className="h-3 w-2/3" />
      </CardContent>
    </Card>
  );
}
