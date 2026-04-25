// Tanstack Query hooks for the Bans feature surface.
//
// Two parallel ban registries are exposed by the controller:
//   - User bans, keyed by username
//   - IP/CIDR bans, keyed by CIDR
//
// Each registry has list/add/remove operations. The shapes here are
// hand-typed against `contracts/api/openapi.yaml` (`/api/bans/*`,
// schemas are `additionalProperties: true` so we keep the strict slice
// the UI renders and let extra fields pass through).

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

const USER_BANS_KEY = ["bans", "users"] as const;
const IP_BANS_KEY = ["bans", "ips"] as const;

const USER_BANS_PATH = "api/bans/users";
const IP_BANS_PATH = "api/bans/ips";

/** A single user ban as returned by `GET /api/bans/users`. */
export interface UserBan {
  username: string;
  reason?: string;
  /** Free-text clarification, optional, supplied at create time. */
  reason_detail?: string;
  /** ISO timestamp the ban was applied. */
  banned_at?: string;
  /** ISO timestamp the ban auto-lifts; absent / empty means indefinite. */
  expires_at?: string;
  /** Operator who applied the ban. */
  actor?: string;
  [key: string]: unknown;
}

/** A single IP/CIDR ban as returned by `GET /api/bans/ips`. */
export interface IpBan {
  cidr: string;
  reason?: string;
  banned_at?: string;
  expires_at?: string;
  actor?: string;
  [key: string]: unknown;
}

interface BansListResponse<T> {
  bans: readonly T[];
}

export interface AddUserBanInput {
  username: string;
  reason: string;
  /** Pass an ISO datetime string to auto-expire; omit / empty for indefinite. */
  expires_at?: string;
  reason_detail?: string;
}

export interface AddIpBanInput {
  cidr: string;
  reason: string;
  expires_at?: string;
}

export function useUserBans(): UseQueryResult<readonly UserBan[]> {
  return useQuery({
    queryKey: USER_BANS_KEY,
    queryFn: async () => {
      const res = await fetcher<BansListResponse<UserBan>>(USER_BANS_PATH);
      return res.bans ?? [];
    },
  });
}

export function useIpBans(): UseQueryResult<readonly IpBan[]> {
  return useQuery({
    queryKey: IP_BANS_KEY,
    queryFn: async () => {
      const res = await fetcher<BansListResponse<IpBan>>(IP_BANS_PATH);
      return res.bans ?? [];
    },
  });
}

export function useAddUserBan(): UseMutationResult<
  unknown,
  Error,
  AddUserBanInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<unknown>(USER_BANS_PATH, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: USER_BANS_KEY });
    },
  });
}

export function useAddIpBan(): UseMutationResult<
  unknown,
  Error,
  AddIpBanInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<unknown>(IP_BANS_PATH, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: IP_BANS_KEY });
    },
  });
}

export function useRemoveUserBan(): UseMutationResult<
  unknown,
  Error,
  { username: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ username }) =>
      fetcher<unknown>(
        `${USER_BANS_PATH}/${encodeURIComponent(username)}/remove`,
        { method: "POST", body: JSON.stringify({ confirm: true }) },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: USER_BANS_KEY });
    },
  });
}

export function useRemoveIpBan(): UseMutationResult<
  unknown,
  Error,
  { cidr: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ cidr }) =>
      fetcher<unknown>(
        `${IP_BANS_PATH}/${encodeURIComponent(cidr)}/remove`,
        { method: "POST", body: JSON.stringify({ confirm: true }) },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: IP_BANS_KEY });
    },
  });
}

export const bansQueryKeys = {
  users: USER_BANS_KEY,
  ips: IP_BANS_KEY,
} as const;
