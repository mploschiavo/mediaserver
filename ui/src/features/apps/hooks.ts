import {
  useQuery,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

/**
 * Service entry as returned by ``GET /api/services`` (controller
 * registry). The launcher only needs the fields below; the live
 * shape carries more (auth_path, version_path, etc.) that the apps
 * page doesn't render.
 */
export interface ServiceEntry {
  id: string;
  name: string;
  desc?: string;
  category?: string;
  host?: string;
  port?: number;
  published_port?: number;
  preserve_path_prefix?: boolean;
  health_path?: string;
}

interface ServicesResponse {
  services: readonly ServiceEntry[];
}

export function useServices(): UseQueryResult<ServicesResponse> {
  return useQuery({
    queryKey: ["services"],
    queryFn: async () => {
      // The controller's ``GET /api/services`` returns a bare JSON
      // list (``[ {id, name, ...}, ... ]``) — not a wrapped
      // ``{services: [...]}`` envelope. Older code in this hook
      // assumed the envelope and saw an empty array on every fresh
      // install: the AppsPage rendered "No launchable apps" even
      // when 28 services were registered. Coerce both shapes here
      // so a future contract change in either direction doesn't
      // break the launcher.
      const raw = await fetcher<unknown>("api/services");
      const arr = Array.isArray(raw)
        ? raw
        : Array.isArray((raw as { services?: unknown })?.services)
          ? (raw as { services: unknown[] }).services
          : [];
      return {
        services: arr.filter(
          (s): s is ServiceEntry =>
            typeof s === "object" &&
            s !== null &&
            typeof (s as ServiceEntry).id === "string",
        ),
      };
    },
    staleTime: 5 * 60_000,
  });
}
