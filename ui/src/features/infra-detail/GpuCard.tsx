import { useMemo } from "react";
import { Cpu, Sparkles, XCircle } from "lucide-react";
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
import { Skeleton } from "@/components/ui/skeleton";
import { asArray } from "@/lib/coerce";
import { useEnableGpu, useGpu, type GpuDevice } from "./hooks";

/**
 * Read whether Intel QSV / VA-API support is detected. The controller
 * may set `intel_qsv` directly (v1.3.0 shape) or expose it via the
 * `gpus[].type` strings — we accept both.
 */
function detectIntel(
  flag: boolean | undefined,
  gpus: readonly GpuDevice[],
): boolean {
  if (flag === true) return true;
  if (flag === false) return false;
  return gpus.some((g) => {
    const t = (g.type ?? "").toLowerCase();
    return t.includes("intel") || t.includes("vaapi") || t.includes("qsv");
  });
}

function detectNvidia(
  flag: boolean | undefined,
  gpus: readonly GpuDevice[],
): boolean {
  if (flag === true) return true;
  if (flag === false) return false;
  return gpus.some((g) => {
    const t = (g.type ?? "").toLowerCase();
    return t.includes("nvidia") || t.includes("nvenc") || t.includes("cuda");
  });
}

export function GpuCard() {
  const query = useGpu();
  const enable = useEnableGpu();

  const data = query.data;
  // Coerce both legacy `devices[]` and OpenAPI `gpus[]` to a single
  // canonical list. asArray() guards a re-fetch returning a non-array.
  const gpus = useMemo<readonly GpuDevice[]>(() => {
    const legacy = asArray<GpuDevice>(data?.devices);
    const canonical = asArray<GpuDevice>(data?.gpus);
    return legacy.length > 0 ? legacy : canonical;
  }, [data]);

  const intel = detectIntel(data?.intel_qsv, gpus);
  const nvidia = detectNvidia(data?.nvidia, gpus);
  const detected =
    typeof data?.detected === "boolean"
      ? data.detected
      : gpus.length > 0 || intel || nvidia;
  const alreadyOn = Boolean(data?.jellyfin_configured && data?.jellyfin_has_gpu);
  const canEnable =
    typeof data?.can_auto_configure === "boolean"
      ? data.can_auto_configure
      : detected && !alreadyOn;

  const handleEnable = () => {
    enable.mutate(undefined, {
      onSuccess: (res) => {
        if (res?.status === "ok") {
          toast.success(res.note ?? "GPU transcoding enabled");
        } else if (res?.error) {
          toast.error(res.error);
        } else {
          toast.success("GPU transcoding enabled");
        }
      },
      onError: (err) => {
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Failed to enable GPU";
        toast.error(msg);
      },
    });
  };

  return (
    <Card data-testid="gpu-card">
      <CardHeader>
        <CardTitle>GPU transcode</CardTitle>
        <CardDescription>
          Hardware acceleration available to Jellyfin
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <div className="flex flex-col gap-2" data-testid="gpu-loading">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="gpu-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              {intel ? (
                <Badge variant="success" data-testid="gpu-badge-intel">
                  <Cpu aria-hidden className="size-3" />
                  Intel QSV / VA-API
                </Badge>
              ) : null}
              {nvidia ? (
                <Badge variant="success" data-testid="gpu-badge-nvidia">
                  <Sparkles aria-hidden className="size-3" />
                  NVIDIA NVENC
                </Badge>
              ) : null}
              {!intel && !nvidia ? (
                <Badge variant="outline" data-testid="gpu-badge-none">
                  <XCircle aria-hidden className="size-3" />
                  No GPU detected
                </Badge>
              ) : null}
              {alreadyOn ? (
                <Badge variant="info" data-testid="gpu-badge-on">
                  Transcode enabled
                </Badge>
              ) : null}
            </div>

            {detected ? (
              <Button
                variant="primary"
                size="sm"
                onClick={handleEnable}
                disabled={!canEnable || alreadyOn}
                loading={enable.isPending}
                data-testid="gpu-enable"
              >
                {alreadyOn ? "Already enabled" : "Enable for transcode"}
              </Button>
            ) : null}

            {gpus.length > 0 ? (
              <ul
                className="flex flex-col gap-1 text-xs"
                data-testid="gpu-device-list"
              >
                {gpus.map((g, i) => {
                  const devs = asArray<string>(g.devices);
                  const key = `${g.type ?? "gpu"}-${i}`;
                  return (
                    <li
                      key={key}
                      className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-bg-1/40 px-2 py-1.5"
                      data-testid={`gpu-device-${i}`}
                    >
                      <span className="font-medium text-fg">
                        {g.name ?? g.type ?? "GPU"}
                      </span>
                      {g.type ? (
                        <span className="font-mono text-fg-muted">
                          {g.type}
                        </span>
                      ) : null}
                      {g.driver ? (
                        <span className="text-fg-muted">drv: {g.driver}</span>
                      ) : null}
                      {devs.length > 0 ? (
                        <span className="font-mono text-fg-muted">
                          {devs.join(", ")}
                        </span>
                      ) : null}
                      {g.container ? (
                        <span className="text-fg-muted">
                          → {g.container}
                        </span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
