#!/usr/bin/env bash
# Sync /etc/hosts so every hostname the running Envoy serves resolves
# to a host-loopback address.
#
# This is the one-command fix for the recurring "DNS_PROBE_FINISHED_NXDOMAIN"
# browser error: Envoy adds a new virtual host (e.g. auth.media-stack.local),
# the generated Envoy config picks it up, but your /etc/hosts still
# points at the OLD hostname (e.g. authelia.media-stack.local). Result:
# the redirect works inside the cluster, the browser can't follow it.
#
# Usage:
#   bin/sync-etc-hosts.sh                  # preview (diff)
#   bin/sync-etc-hosts.sh --apply          # write (requires sudo)
#   bin/sync-etc-hosts.sh --ip 192.168.1.60 --apply
#
# The script:
#   1. Reads the live Envoy config from the envoy container.
#   2. Extracts every vhost domain under *.media-stack.local.
#   3. Replaces the controller-managed block in /etc/hosts (delimited
#      with BEGIN/END media-stack markers).
set -Eeuo pipefail

IP="127.0.0.1"
APPLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ip) IP="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help)
      sed -n '1,/^set -/p' "$0" | sed '$d' | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "[ERR] unknown flag: $1" >&2; exit 2 ;;
  esac
done

BEGIN_MARKER="# >>> media-stack hostnames — managed by bin/sync-etc-hosts.sh >>>"
END_MARKER="# <<< media-stack hostnames <<<"

if ! docker ps --format '{{.Names}}' | grep -q '^envoy$'; then
  echo "[ERR] envoy container not running. Start the stack first." >&2
  exit 1
fi

# Pull every hostname the Envoy config declares.
hostnames=$(docker exec envoy grep -oE '[a-z0-9-]+\.media-stack\.local' \
  /etc/envoy/envoy.yaml | sort -u)
if [[ -z "$hostnames" ]]; then
  echo "[ERR] no *.media-stack.local hostnames found in Envoy config." >&2
  exit 1
fi

new_block=$(printf '%s\n%s  %s\n%s\n' \
  "$BEGIN_MARKER" \
  "$IP" \
  "$(echo "$hostnames" | tr '\n' ' ')" \
  "$END_MARKER")

# Build the proposed /etc/hosts by stripping any existing managed block
# and appending the fresh one.
existing=$(cat /etc/hosts)
stripped=$(printf '%s\n' "$existing" \
  | awk -v b="$BEGIN_MARKER" -v e="$END_MARKER" '
      $0 == b { skip=1; next }
      $0 == e { skip=0; next }
      !skip   { print }
    ')
proposed=$(printf '%s\n\n%s\n' "$stripped" "$new_block")

echo "[INFO] Envoy serves $(echo "$hostnames" | wc -l) hostnames."
echo
echo "=== diff of proposed /etc/hosts ==="
diff <(printf '%s\n' "$existing") <(printf '%s\n' "$proposed") || true
echo

if [[ "$APPLY" != "1" ]]; then
  echo "[INFO] dry-run only. Re-run with --apply (and sudo) to commit."
  exit 0
fi

TMP=$(mktemp)
printf '%s\n' "$proposed" > "$TMP"
# Basic sanity: final must still be parseable.
if ! awk 'NF && !/^#/ { print $1 }' "$TMP" | head -1 >/dev/null; then
  echo "[ERR] proposed /etc/hosts looks malformed; aborting." >&2
  rm -f "$TMP"
  exit 1
fi

sudo cp /etc/hosts "/etc/hosts.bak.$(date +%Y%m%dT%H%M%S)"
sudo cp "$TMP" /etc/hosts
rm -f "$TMP"
echo "[OK] /etc/hosts updated. Original backed up to /etc/hosts.bak.*"
