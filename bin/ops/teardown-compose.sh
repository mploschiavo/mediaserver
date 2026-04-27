#!/usr/bin/env bash
# Tear down a docker-compose deployment of the media stack.
#
# Three scopes:
#   --config-only  Stop containers, wipe runtime config dirs (sonarr/radarr/
#                  jellyfin/etc.), preserve git-tracked config/defaults/
#                  AND user data (data/torrents, data/usenet). Default.
#   --with-data    Also wipe the data/ tree (torrents, usenet, transcode).
#                  Skips /media so downloaded films/shows survive.
#   --everything   Wipe config/, data/, AND prompt before /media/.
#
# Always:
#   * Kills stale ``kubectl port-forward`` processes that bind compose
#     host ports (a real gotcha — port-forwards left over from k8s
#     work block ``docker compose up`` until manually torn down).
#   * Refuses to delete config/defaults/ — it's git-tracked bootstrap
#     templates the controller reads on first run. ``rm -rf config/``
#     in the legacy how-to nukes them too; this script is the safe
#     replacement.
#
# Use --dry-run to see what would happen without taking action.
#
# Re-deploys NOT included — once teardown finishes, run
# ``docker compose -f deploy/compose/docker-compose.yml up -d`` for a
# fresh bootstrap.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO_ROOT/deploy/compose/docker-compose.yml}"
CONFIG_ROOT="${CONFIG_ROOT:-$REPO_ROOT/config}"
DATA_ROOT="${DATA_ROOT:-$REPO_ROOT/data}"
MEDIA_ROOT="${MEDIA_ROOT:-$REPO_ROOT/media}"

SCOPE="config-only"
DRY_RUN=false
ASSUME_YES=false

usage() {
  cat <<'EOF'
Usage: bin/ops/teardown-compose.sh [SCOPE] [--dry-run] [--yes]

Scopes (pick one):
  --config-only   Wipe config/ runtime dirs, keep config/defaults/ and data/.
                  Default if no scope is given.
  --with-data     Also wipe data/ (torrents, usenet, transcode).
  --everything    Wipe config/, data/, and (with confirmation) media/.

Flags:
  --dry-run       Show what would be deleted; take no action.
  --yes           Don't prompt before destructive operations.
  -h, --help      Print this and exit.

Environment overrides:
  COMPOSE_FILE    Path to docker-compose.yml (default: deploy/compose/...)
  CONFIG_ROOT     Path to config/ (default: $REPO_ROOT/config)
  DATA_ROOT       Path to data/   (default: $REPO_ROOT/data)
  MEDIA_ROOT     Path to media/  (default: $REPO_ROOT/media)
EOF
}

for arg in "$@"; do
  case "$arg" in
    --config-only) SCOPE="config-only" ;;
    --with-data)   SCOPE="with-data" ;;
    --everything)  SCOPE="everything" ;;
    --dry-run)     DRY_RUN=true ;;
    --yes|-y)      ASSUME_YES=true ;;
    -h|--help)     usage; exit 0 ;;
    *)
      echo "[ERR] Unknown argument: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

run() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    echo "[RUN] $*"
    "$@"
  fi
}

confirm() {
  $ASSUME_YES && return 0
  $DRY_RUN && return 0
  local prompt="${1:-Continue?}"
  read -r -p "$prompt [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

echo "=================================="
echo " Media-stack compose teardown"
echo "=================================="
echo "  Scope:        $SCOPE"
echo "  Compose file: $COMPOSE_FILE"
echo "  CONFIG_ROOT:  $CONFIG_ROOT"
echo "  DATA_ROOT:    $DATA_ROOT"
[[ "$SCOPE" == "everything" ]] && echo "  MEDIA_ROOT:   $MEDIA_ROOT"
$DRY_RUN && echo "  Mode:         DRY RUN"
echo

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "[ERR] Compose file not found: $COMPOSE_FILE" >&2
  exit 1
fi

# --- 1. Stop and remove containers + networks ---
if confirm "Stop and remove every compose container?"; then
  run docker compose -f "$COMPOSE_FILE" down --remove-orphans
fi

# --- 2. Kill stale kubectl port-forwards holding compose host ports ---
# This is the silent failure mode: a left-over ``kubectl port-forward``
# from prior k8s work binds 127.0.0.1:8080 (or any other compose host
# port), and ``docker compose up`` errors with "address already in use"
# without telling the operator the holder is kubectl.
echo "[INFO] Scanning for stale kubectl port-forwards on common compose ports..."
PORTS_TO_CHECK=(8080 8989 7878 6767 8686 8787 9117)
for port in "${PORTS_TO_CHECK[@]}"; do
  pids="$(lsof -ti ":$port" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    for pid in $pids; do
      cmd="$(ps -p "$pid" -o cmd= 2>/dev/null || true)"
      if [[ "$cmd" == *kubectl*port-forward* ]]; then
        echo "[INFO] Killing kubectl port-forward holding :$port (pid $pid)"
        run kill "$pid" || true
      fi
    done
  fi
done

# --- 3. Wipe config/ subdirs except defaults/ ---
if [[ -d "$CONFIG_ROOT" ]]; then
  echo
  echo "[INFO] Wiping $CONFIG_ROOT/* (keeping defaults/ — it's git-tracked)"
  if confirm "Delete every subdir of $CONFIG_ROOT except defaults/?"; then
    # Use find -mindepth 1 so we never rm the dir itself.
    if $DRY_RUN; then
      find "$CONFIG_ROOT" -mindepth 1 -maxdepth 1 -not -name defaults -print
    else
      find "$CONFIG_ROOT" -mindepth 1 -maxdepth 1 -not -name defaults \
        -exec rm -rf {} +
    fi
  fi
else
  echo "[INFO] $CONFIG_ROOT does not exist — nothing to wipe."
fi

# --- 4. Wipe data/ if scope ≥ with-data ---
if [[ "$SCOPE" == "with-data" || "$SCOPE" == "everything" ]]; then
  if [[ -d "$DATA_ROOT" ]]; then
    echo
    echo "[WARN] $DATA_ROOT contains active torrent / usenet state."
    if confirm "Wipe $DATA_ROOT (torrents, usenet, transcode)?"; then
      run rm -rf "$DATA_ROOT"
    fi
  fi
fi

# --- 5. Wipe media/ only on --everything (and only with confirm) ---
if [[ "$SCOPE" == "everything" ]]; then
  if [[ -d "$MEDIA_ROOT" ]]; then
    echo
    echo "[WARN] $MEDIA_ROOT contains downloaded films/shows."
    if confirm "REALLY wipe $MEDIA_ROOT?"; then
      run rm -rf "$MEDIA_ROOT"
    fi
  fi
fi

echo
echo "[OK] Teardown complete."
echo
echo "Next steps:"
echo "  docker compose -f $COMPOSE_FILE up -d"
echo "  # Watch bootstrap finish:"
echo "  docker compose -f $COMPOSE_FILE logs -f media-stack-controller"
