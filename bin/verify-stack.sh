#!/usr/bin/env bash
# One-command end-to-end health check of the media-stack compose deployment.
#
# Runs the exact sequence a user's browser would, and reports PASS/FAIL
# per step. The goal is: "after ANY change to the stack, run this once
# and know whether `https://apps.media-stack.local/` actually works
# for a real browser — or what's broken."
#
# Checks (in order):
#   1. Envoy container is up and healthy
#   2. Envoy config on disk has a TLS listener
#   3. Every *.media-stack.local vhost Envoy serves resolves on this host
#   4. Envoy on :443 answers with a valid TLS certificate
#   5. `GET apps.media-stack.local/` returns 302 with a Location that
#      points at auth.media-stack.local (root path, no `/app` corruption)
#   6. The Location host itself resolves on this machine
#   7. Authelia login page loads at that location
#
# Exit codes:
#   0  everything green
#   1  one or more checks failed (summary printed)
#   2  prerequisite missing (docker, curl)
#
# Usage:
#   bin/verify-stack.sh                          # full check
#   bin/verify-stack.sh --only 5                 # run only step 5
#   bin/verify-stack.sh --host apps.other.local  # non-default gateway
set -Eeuo pipefail

GATEWAY_HOST="apps.media-stack.local"
ONLY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) GATEWAY_HOST="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,/^set -/p' "$0" | sed '$d' | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "[ERR] unknown flag: $1" >&2; exit 2 ;;
  esac
done

for bin in docker curl openssl; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "[ERR] '$bin' not found in PATH" >&2
    exit 2
  fi
done

RESULTS=()

record() {
  # $1 = step label, $2 = pass|fail|skip, $3 = detail
  RESULTS+=("$1|$2|$3")
  local color
  case "$2" in
    pass) color='\033[32m' ;;
    fail) color='\033[31m' ;;
    skip) color='\033[33m' ;;
    *)    color='' ;;
  esac
  printf "  ${color}%-4s\033[0m %s — %s\n" "${2^^}" "$1" "$3"
}

step_active() { [[ -z "$ONLY" || "$ONLY" == "$1" ]]; }

echo "=== media-stack verify ($GATEWAY_HOST) ==="
echo

# Step 1: Envoy container is up and healthy.
if step_active 1; then
  status=$(docker inspect envoy --format '{{.State.Health.Status}}' 2>/dev/null || echo "missing")
  if [[ "$status" == "healthy" ]]; then
    record "1. Envoy container healthy" pass "status=$status"
  else
    record "1. Envoy container healthy" fail "status=$status"
  fi
fi

# Step 2: Envoy config has a TLS listener.
if step_active 2; then
  count=$(docker exec envoy grep -c "transport_socket:" /etc/envoy/envoy.yaml 2>/dev/null || echo 0)
  if [[ "$count" -gt 0 ]]; then
    record "2. Envoy config TLS listener" pass "transport_socket blocks=$count"
  else
    record "2. Envoy config TLS listener" fail \
      "no transport_socket in /etc/envoy/envoy.yaml — run 'docker compose run --rm envoy-config-init' then restart envoy"
  fi
fi

# Step 3: Every vhost hostname resolves.
if step_active 3; then
  unresolved=()
  while IFS= read -r host; do
    [[ -z "$host" ]] && continue
    if ! getent hosts "$host" >/dev/null 2>&1; then
      unresolved+=("$host")
    fi
  done < <(docker exec envoy grep -oE '[a-z0-9-]+\.media-stack\.local' \
             /etc/envoy/envoy.yaml 2>/dev/null | sort -u)
  if [[ "${#unresolved[@]}" -eq 0 ]]; then
    record "3. All vhost hostnames resolve" pass "checked $(docker exec envoy grep -oE '[a-z0-9-]+\.media-stack\.local' /etc/envoy/envoy.yaml | sort -u | wc -l) hosts"
  else
    record "3. All vhost hostnames resolve" fail \
      "missing from /etc/hosts: ${unresolved[*]} — run 'bin/sync-etc-hosts.sh --apply'"
  fi
fi

# Step 4: TLS handshake + cert info. Self-signed certs are expected
# on the compose path, so we skip cert-chain verification — we only
# care that (a) the handshake succeeds and (b) a cert was served.
if step_active 4; then
  cert=$(echo | openssl s_client -connect "127.0.0.1:443" \
           -servername "$GATEWAY_HOST" 2>/dev/null \
         | openssl x509 -noout -subject -enddate 2>/dev/null || true)
  if [[ -n "$cert" ]]; then
    record "4. Gateway TLS handshake" pass "$(echo "$cert" | tr '\n' ' ')"
  else
    record "4. Gateway TLS handshake" fail \
      "TLS handshake failed — Envoy likely has a plain-HTTP listener. See step 2."
  fi
fi

# Step 5: Gateway redirects to Authelia with a correct portal URL.
if step_active 5; then
  location=$(curl -skI --resolve "$GATEWAY_HOST:443:127.0.0.1" \
               "https://$GATEWAY_HOST/" 2>/dev/null \
             | awk 'tolower($1)=="location:"{print $2}' | tr -d '\r')
  if [[ -z "$location" ]]; then
    record "5. Gateway returns redirect" fail "no Location header on /"
  elif [[ "$location" =~ ^https://auth\..+/\?rd= ]]; then
    record "5. Gateway redirects to portal root" pass "$location"
  elif [[ "$location" =~ ^https://.+/app.*\?rd= ]]; then
    record "5. Gateway redirects to portal root" fail \
      "Location has /app in the path — ext_authz rd-append bug regression: $location"
  else
    record "5. Gateway redirects to portal root" fail "unexpected Location: $location"
  fi
fi

# Step 6: The login host itself resolves.
if step_active 6; then
  if [[ -n "${location:-}" ]]; then
    login_host=$(echo "$location" | awk -F/ '{print $3}' | cut -d: -f1)
    if getent hosts "$login_host" >/dev/null 2>&1; then
      record "6. Login host resolves" pass "$login_host"
    else
      record "6. Login host resolves" fail \
        "$login_host not in /etc/hosts — browser will show DNS_PROBE_FINISHED_NXDOMAIN"
    fi
  else
    record "6. Login host resolves" skip "no Location from step 5"
  fi
fi

# Step 7: Authelia login page loads.
if step_active 7; then
  if [[ -n "${login_host:-}" ]]; then
    code=$(curl -sk -o /dev/null -w "%{http_code}" \
             --resolve "$login_host:443:127.0.0.1" \
             "https://$login_host/" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
      record "7. Authelia login reachable" pass "HTTP $code"
    else
      record "7. Authelia login reachable" fail "HTTP $code on $login_host/"
    fi
  else
    record "7. Authelia login reachable" skip "no login host from step 6"
  fi
fi

echo
passes=0; fails=0; skips=0
for r in "${RESULTS[@]}"; do
  case "$(echo "$r" | cut -d'|' -f2)" in
    pass) passes=$((passes+1)) ;;
    fail) fails=$((fails+1)) ;;
    skip) skips=$((skips+1)) ;;
  esac
done
echo "=== $passes pass / $fails fail / $skips skip ==="
if [[ "$fails" -gt 0 ]]; then
  echo
  echo "To fix most issues in one go:"
  echo "  docker compose -f docker/docker-compose.yml -p media-stack run --rm envoy-config-init"
  echo "  docker restart envoy"
  echo "  bin/sync-etc-hosts.sh --apply"
  exit 1
fi
exit 0
