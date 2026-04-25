import { asArray } from "@/lib/coerce";
import { useState } from "react";
import { Check, Copy, Server } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/layout/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { useGatewayHostnames } from "./hooks";

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through
  }
  return false;
}

interface CopyButtonProps {
  value: string;
  testId: string;
}

function CopyButton({ value, testId }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      size="sm"
      data-testid={testId}
      aria-label={`Copy ${value}`}
      onClick={async () => {
        const ok = await copyToClipboard(value);
        if (ok) {
          setCopied(true);
          toast.success(`Copied ${value}`);
          window.setTimeout(() => setCopied(false), 1200);
        } else {
          toast.error("Clipboard unavailable");
        }
      }}
    >
      {copied ? (
        <Check className="size-4 text-success" aria-hidden />
      ) : (
        <Copy className="size-4" aria-hidden />
      )}
    </Button>
  );
}

/**
 * Gateway hostnames — read-only inventory of every hostname Envoy is
 * configured to terminate. Each row gets a copy-to-clipboard so the
 * operator can paste straight into a TLS-cert SAN list / DNS panel.
 */
export function GatewayHostnamesCard() {
  const query = useGatewayHostnames();
  const hostnames = asArray(query.data?.hostnames);

  return (
    <Card data-testid="gateway-hostnames-card">
      <CardHeader>
        <CardTitle>Gateway hostnames</CardTitle>
        <CardDescription>
          Hostnames the Envoy gateway is configured to serve.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.error ? (
          <div
            role="alert"
            data-testid="gateway-hostnames-error"
            className="rounded-md border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-3 text-sm text-danger"
          >
            <p className="font-medium">Failed to load hostnames</p>
            <p className="mt-1 text-fg-muted">{query.error.message}</p>
          </div>
        ) : query.isLoading ? (
          <div className="space-y-2" data-testid="gateway-hostnames-loading">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : hostnames.length === 0 ? (
          <EmptyState
            icon={Server}
            title="No hostnames"
            description="Envoy has not advertised any virtual-host configuration."
          />
        ) : (
          <ul
            className="flex flex-col divide-y divide-border rounded-md border border-border"
            data-testid="gateway-hostnames-rows"
          >
            {hostnames.map((hostname) => (
              <li
                key={hostname}
                className="flex items-center justify-between gap-2 px-3 py-2"
                data-testid={`gateway-hostname-${hostname}`}
              >
                <span className="truncate font-mono text-sm text-fg">
                  {hostname}
                </span>
                <CopyButton
                  value={hostname}
                  testId={`gateway-hostname-copy-${hostname}`}
                />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
