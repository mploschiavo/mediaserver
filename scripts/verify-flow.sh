#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${1:-${NAMESPACE:-media-stack}}"

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  echo "[ERR] Neither microk8s nor kubectl found in PATH." >&2
  exit 1
fi

ok() { echo "[OK] $*"; }
warn() { echo "[WARN] $*"; }
err() { echo "[ERR] $*" >&2; exit 1; }

"${KUBECTL[@]}" -n "$NAMESPACE" get pods >/dev/null || err "Namespace '$NAMESPACE' is not reachable"

BOOT_LOG="$("${KUBECTL[@]}" -n "$NAMESPACE" logs job/media-stack-bootstrap --tail=500 2>/dev/null || true)"
if [[ -z "$BOOT_LOG" ]]; then
  err "No bootstrap logs found. Run: bash scripts/run-bootstrap-job.sh"
fi

check_log() {
  local regex="$1"
  local label="$2"
  if echo "$BOOT_LOG" | grep -Eq "$regex"; then
    ok "$label"
  else
    warn "$label"
  fi
}

check_log_optional() {
  local regex="$1"
  local label="$2"
  if echo "$BOOT_LOG" | grep -Eq "$regex"; then
    ok "$label"
  else
    echo "[INFO] $label (not configured)"
  fi
}

echo "Namespace: $NAMESPACE"
"${KUBECTL[@]}" -n "$NAMESPACE" get pods

echo
ok "Flow checks from latest bootstrap log"
for app in Sonarr Radarr Lidarr Readarr; do
  check_log "Prowlarr: updated application link for ${app}" "${app} <- Prowlarr app link"
  check_log "${app}: (updated|created|reconciled existing named) qBittorrent download client" "${app} -> qBittorrent client wired"
  check_log_optional "${app}: (updated|created|reconciled existing named) SABnzbd download client" "${app} -> SABnzbd client wired"
  check_log_optional "${app}: remote path mapping (created|updated|already set)" "${app} SAB remote path mappings reconciled"
  check_log_optional "${app}: discovery list reconcile complete" "${app} discovery lists reconciled"
  check_log "${app}: (updated media management|media management already set).*hardlinks=True" "${app} hardlinks policy enforced"
  check_log "${app}: (updated download handling|download handling already set)" "${app} CDH enabled"
  check_log "${app}: (updated download handling|download handling already set).*removeFailed=True.*autoRedownloadFailed=True" "${app} self-healing failed-download retry enabled"
done

check_log_optional "Lidarr: (created|updated) discovery list 'Last.fm Top Rock Artists \\(Top 100\\)'" "Lidarr top-100 music curation list reconciled"
check_log_optional "Lidarr: (created|updated) discovery list 'Last.fm Top Pop Artists \\(Top 100\\)'" "Lidarr pop top-100 curation list reconciled"
check_log_optional "Readarr: (created|updated) discovery list 'Goodreads Best Books Ever'" "Readarr books curation list reconciled"
check_log_optional "Readarr: (created|updated) discovery list 'Goodreads Popular Science Fiction'" "Readarr sci-fi books curation list reconciled"

check_log "Jellyseerr: configured Jellyfin connection" "Jellyseerr -> Jellyfin wired"
check_log "Jellyseerr: (updated|created|existing) (Radarr|Sonarr)" "Jellyseerr -> Arr mappings wired"
check_log "Jellyfin libraries: reconcile complete" "Jellyfin libraries reconciled"
check_log_optional "Jellyfin libraries: tuned 'Movies' options" "Jellyfin Movies tuning applied"
check_log_optional "Jellyfin libraries: tuned 'TV Shows' options" "Jellyfin TV tuning applied"
check_log_optional "Jellyfin libraries: triggered library refresh" "Jellyfin library refresh triggered"
check_log "Jellyfin Live TV: reconcile complete" "Jellyfin Live TV reconciled"
check_log_optional "Jellyfin Live TV: requested channel refresh" "Jellyfin Live TV channel refresh requested"
check_log_optional "Jellyfin Live TV: requested guide refresh" "Jellyfin Live TV guide refresh requested"
check_log "Jellyfin plugins: reconcile complete" "Jellyfin plugins reconciled"
check_log_optional "Jellyfin playback: reconcile complete" "Jellyfin playback defaults reconciled"
check_log_optional "Jellyfin home rails: reconcile complete" "Jellyfin curated home rails reconciled"
check_log_optional "Jellyfin home rails: disabled; cleaned up synthetic collections" "Jellyfin synthetic rail collections cleaned up"
check_log_optional "Disk guardrails: usage check" "Disk guardrails usage checks logged"
check_log_optional "Disk guardrails: deleted completed qB torrents" "Disk guardrails qB cleanup executed when needed"
check_log_optional "Homepage: (wrote services config|services config already up-to-date)" "Homepage services config reconciled"
check_log_optional "Bazarr: (wrote integration config|Sonarr/Radarr integration already matches desired config|Sonarr/Radarr \\+ subtitle automation config already matches desired state)" "Bazarr Sonarr/Radarr integration reconciled"

echo
ok "Live config checks"
if "${KUBECTL[@]}" -n "$NAMESPACE" get cronjob media-stack-bootstrap-reconcile >/dev/null 2>&1; then
  cron_schedule="$("${KUBECTL[@]}" -n "$NAMESPACE" get cronjob media-stack-bootstrap-reconcile -o jsonpath='{.spec.schedule}' 2>/dev/null || true)"
  ok "Bootstrap reconcile CronJob present (schedule=${cron_schedule:-unknown})"
else
  warn "Bootstrap reconcile CronJob present"
fi

INGRESS_HOSTS="$("${KUBECTL[@]}" -n "$NAMESPACE" get ingress media-stack-ingress -o jsonpath='{range .spec.rules[*]}{.host}{" "}{end}' 2>/dev/null || true)"
HP_SERVICES="$("${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/homepage -- sh -lc 'cat /app/config/services.yaml 2>/dev/null' || true)"
if [[ -z "$INGRESS_HOSTS" || -z "$HP_SERVICES" ]]; then
  warn "Homepage ingress/service check skipped (missing ingress hosts or homepage config)"
else
  missing_hosts=0
  for h in $INGRESS_HOSTS; do
    if echo "$HP_SERVICES" | grep -q "$h"; then
      :
    else
      missing_hosts=$((missing_hosts + 1))
      warn "Homepage services.yaml missing host: $h"
    fi
  done
  if [[ "$missing_hosts" -eq 0 ]]; then
    ok "Homepage services.yaml contains all ingress hosts"
  fi

  if echo "$HP_SERVICES" | grep -q "Jellyfin Setup QR"; then
    ok "Homepage onboarding includes Jellyfin QR card"
  else
    warn "Homepage onboarding includes Jellyfin QR card"
  fi
  if echo "$HP_SERVICES" | grep -q "Samsung TV Quick Start"; then
    ok "Homepage onboarding includes Samsung quick steps"
  else
    warn "Homepage onboarding includes Samsung quick steps"
  fi
  if echo "$HP_SERVICES" | grep -q "Vizio Quick Start"; then
    ok "Homepage onboarding includes Vizio quick steps"
  else
    warn "Homepage onboarding includes Vizio quick steps"
  fi
  if echo "$HP_SERVICES" | grep -q "TCL Quick Start"; then
    ok "Homepage onboarding includes TCL quick steps"
  else
    warn "Homepage onboarding includes TCL quick steps"
  fi
fi

JELLYFIN_API_KEY="$("${KUBECTL[@]}" -n "$NAMESPACE" get secret media-stack-secrets -o jsonpath='{.data.JELLYFIN_API_KEY}' 2>/dev/null | base64 -d || true)"
JELLYFIN_USER_ID="$("${KUBECTL[@]}" -n "$NAMESPACE" get secret media-stack-secrets -o jsonpath='{.data.JELLYFIN_USER_ID}' 2>/dev/null | base64 -d || true)"
if [[ -z "$JELLYFIN_API_KEY" || -z "$JELLYFIN_USER_ID" ]]; then
  warn "Jellyfin API/user checks skipped (JELLYFIN_API_KEY or JELLYFIN_USER_ID missing in secret)"
elif ! command -v jq >/dev/null 2>&1; then
  warn "Jellyfin API/user checks skipped (jq not installed)"
else
  JF_USER_JSON="$("${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/jellyfin -- sh -lc "curl -fsS \"http://localhost:8096/Users/${JELLYFIN_USER_ID}?api_key=${JELLYFIN_API_KEY}\"" 2>/dev/null || true)"
  JF_SERVER_JSON="$("${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/jellyfin -- sh -lc "curl -fsS \"http://localhost:8096/System/Configuration?api_key=${JELLYFIN_API_KEY}\"" 2>/dev/null || true)"
  JF_VF_JSON="$("${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/jellyfin -- sh -lc "curl -fsS \"http://localhost:8096/Library/VirtualFolders?api_key=${JELLYFIN_API_KEY}\"" 2>/dev/null || true)"
  JF_DP_JSON="$("${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/jellyfin -- sh -lc "curl -fsS \"http://localhost:8096/DisplayPreferences/usersettings?api_key=${JELLYFIN_API_KEY}&userId=${JELLYFIN_USER_ID}&client=emby\"" 2>/dev/null || true)"
  JF_PLUGINS_JSON="$("${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/jellyfin -- sh -lc "curl -fsS \"http://localhost:8096/Plugins?api_key=${JELLYFIN_API_KEY}\"" 2>/dev/null || true)"

  if [[ -n "$JF_USER_JSON" ]] && echo "$JF_USER_JSON" | jq -e '.Configuration.SubtitleMode == "Smart"' >/dev/null 2>&1; then
    ok "Jellyfin playback: SubtitleMode default is Smart"
  else
    warn "Jellyfin playback: SubtitleMode default is Smart"
  fi
  if [[ -n "$JF_USER_JSON" ]] && echo "$JF_USER_JSON" | jq -e '.Configuration.AudioLanguagePreference == "eng"' >/dev/null 2>&1; then
    ok "Jellyfin playback: Audio language default is eng"
  else
    warn "Jellyfin playback: Audio language default is eng"
  fi
  if [[ -n "$JF_SERVER_JSON" ]] && echo "$JF_SERVER_JSON" | jq -e '.PreferredMetadataLanguage == "en"' >/dev/null 2>&1; then
    ok "Jellyfin playback: metadata language default is en"
  else
    warn "Jellyfin playback: metadata language default is en"
  fi
  if [[ -n "$JF_SERVER_JSON" ]] && echo "$JF_SERVER_JSON" | jq -e '.EnableGroupingMoviesIntoCollections == true and .EnableGroupingShowsIntoCollections == true' >/dev/null 2>&1; then
    ok "Jellyfin server: collections grouping is enabled"
  else
    warn "Jellyfin server: collections grouping is enabled"
  fi
  if [[ -n "$JF_SERVER_JSON" ]] && echo "$JF_SERVER_JSON" | jq -e '.TrickplayOptions.ScanBehavior == "NonBlocking" and (.TrickplayOptions.Interval|tonumber) >= 1000' >/dev/null 2>&1; then
    ok "Jellyfin server: trickplay defaults are configured"
  else
    warn "Jellyfin server: trickplay defaults are configured"
  fi
  if [[ -n "$JF_DP_JSON" ]] && echo "$JF_DP_JSON" | jq -e '.ShowBackdrop == true' >/dev/null 2>&1; then
    ok "Jellyfin display: backdrops enabled for usersettings"
  else
    warn "Jellyfin display: backdrops enabled for usersettings"
  fi
  if [[ -n "$JF_DP_JSON" ]] && echo "$JF_DP_JSON" | jq -e '.CustomPrefs.enableThemeVideos == "True"' >/dev/null 2>&1; then
    ok "Jellyfin display: theme videos preference enabled"
  else
    warn "Jellyfin display: theme videos preference enabled"
  fi
  if [[ -n "$JF_DP_JSON" ]] && echo "$JF_DP_JSON" | jq -e '.CustomPrefs.enableNextVideoInfoOverlay == "True"' >/dev/null 2>&1; then
    ok "Jellyfin display: cinematic next-video overlay preference enabled"
  else
    warn "Jellyfin display: cinematic next-video overlay preference enabled"
  fi
  if [[ -n "$JF_PLUGINS_JSON" ]] && echo "$JF_PLUGINS_JSON" | jq -e 'any(.[]; (.Name == "Intro Skipper") and (.Status == "Active"))' >/dev/null 2>&1; then
    ok "Jellyfin plugin active: Intro Skipper"
  else
    warn "Jellyfin plugin active: Intro Skipper"
  fi
  if [[ -n "$JF_PLUGINS_JSON" ]] && echo "$JF_PLUGINS_JSON" | jq -e 'any(.[]; (.Name == "TMDb Box Sets") and (.Status == "Active"))' >/dev/null 2>&1; then
    ok "Jellyfin plugin active: TMDb Box Sets"
  else
    warn "Jellyfin plugin active: TMDb Box Sets"
  fi

  check_jellyfin_library_flag() {
    local collection_type="$1"
    local jq_expr="$2"
    local label="$3"
    if [[ -n "$JF_VF_JSON" ]] && echo "$JF_VF_JSON" | jq -e --arg TYPE "$collection_type" "$jq_expr" >/dev/null 2>&1; then
      ok "$label"
    else
      warn "$label"
    fi
  }
  check_jellyfin_library_flag "movies" 'map(select(.CollectionType==$TYPE))[0].LibraryOptions.EnableRealtimeMonitor == true' "Jellyfin Movies: realtime monitoring enabled"
  check_jellyfin_library_flag "tvshows" 'map(select(.CollectionType==$TYPE))[0].LibraryOptions.EnableRealtimeMonitor == true' "Jellyfin TV: realtime monitoring enabled"
  check_jellyfin_library_flag "movies" 'map(select(.CollectionType==$TYPE))[0].LibraryOptions.EnableTrickplayImageExtraction == true' "Jellyfin Movies: preview thumbnails enabled"
  check_jellyfin_library_flag "tvshows" 'map(select(.CollectionType==$TYPE))[0].LibraryOptions.EnableTrickplayImageExtraction == true' "Jellyfin TV: preview thumbnails enabled"
  check_jellyfin_library_flag "movies" 'map(select(.CollectionType==$TYPE))[0].LibraryOptions.TypeOptions[0].MetadataFetcherOrder[0] == "TheMovieDb"' "Jellyfin Movies: TMDb metadata priority first"
  check_jellyfin_library_flag "movies" 'map(select(.CollectionType==$TYPE))[0].LibraryOptions.TypeOptions[0].ImageOptions | any(.Type=="Backdrop" and (.Limit|tonumber) >= 1)' "Jellyfin Movies: backdrop artwork enabled"
  check_jellyfin_library_flag "movies" 'map(select(.CollectionType==$TYPE))[0].LibraryOptions.TypeOptions[0].ImageOptions | any(.Type=="Logo" and (.Limit|tonumber) >= 1)' "Jellyfin Movies: logo artwork enabled"

  check_jellyfin_collection() {
    local collection_name="$1"
    local query_name
    query_name="$(printf '%s' "$collection_name" | sed 's/ /%20/g')"
    local payload
    payload="$("${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/jellyfin -- sh -lc "curl -fsS \"http://localhost:8096/Items?api_key=${JELLYFIN_API_KEY}&userId=${JELLYFIN_USER_ID}&includeItemTypes=BoxSet&recursive=true&searchTerm=${query_name}&limit=200\"" 2>/dev/null || true)"
    if [[ -n "$payload" ]] && echo "$payload" | jq -e --arg NAME "$collection_name" '(.Items // []) | any(.Name == $NAME)' >/dev/null 2>&1; then
      ok "Jellyfin rail collection exists: ${collection_name}"
    else
      warn "Jellyfin rail collection exists: ${collection_name}"
    fi
  }

  if echo "$BOOT_LOG" | grep -Eq "Jellyfin home rails: reconcile complete"; then
    echo
    ok "Jellyfin curated rail checks"
    check_jellyfin_collection "Trending"
    check_jellyfin_collection "Top Rated"
    check_jellyfin_collection "New This Week"
    check_jellyfin_collection "Because You Watched"
    check_jellyfin_collection "Trending TV"
    check_jellyfin_collection "Top Music"
    check_jellyfin_collection "Top Books"
  else
    echo "[INFO] Jellyfin curated rail collection checks skipped (collections mode disabled)."
  fi
fi

BAZARR_CFG="$("${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/bazarr -- sh -lc 'cat /config/config/config.yaml 2>/dev/null' || true)"
if [[ -z "$BAZARR_CFG" ]]; then
  warn "Bazarr config check skipped (config file not readable)"
else
  get_yaml_value() {
    local section="$1"
    local key="$2"
    echo "$BAZARR_CFG" | awk -v sec="$section" -v k="$key" '
      /^[A-Za-z0-9_]+:/ {
        current=$1
        sub(":", "", current)
      }
      current==sec && $1==(k ":") {
        print $2
        exit
      }
    '
  }

  baz_use_sonarr="$(get_yaml_value general use_sonarr)"
  baz_use_radarr="$(get_yaml_value general use_radarr)"
  baz_sonarr_key="$(get_yaml_value sonarr apikey)"
  baz_radarr_key="$(get_yaml_value radarr apikey)"
  baz_sonarr_ip="$(get_yaml_value sonarr ip)"
  baz_radarr_ip="$(get_yaml_value radarr ip)"
  baz_series_default="$(get_yaml_value general serie_default_enabled)"
  baz_movie_default="$(get_yaml_value general movie_default_enabled)"
  baz_single_language="$(get_yaml_value general single_language)"

  if [[ "$baz_use_sonarr" == "true" && "$baz_use_radarr" == "true" && -n "$baz_sonarr_key" && "$baz_sonarr_key" != "''" && -n "$baz_radarr_key" && "$baz_radarr_key" != "''" ]]; then
    ok "Bazarr Sonarr/Radarr integration is configured (enabled + api keys present)"
  else
    warn "Bazarr Sonarr/Radarr integration appears incomplete (use_sonarr=$baz_use_sonarr use_radarr=$baz_use_radarr)"
  fi

  if [[ "$baz_sonarr_ip" == "sonarr" && "$baz_radarr_ip" == "radarr" ]]; then
    ok "Bazarr points to in-cluster Sonarr/Radarr service hosts"
  else
    warn "Bazarr hosts are unexpected (sonarr.ip=$baz_sonarr_ip radarr.ip=$baz_radarr_ip)"
  fi

  if [[ "$baz_series_default" == "true" && "$baz_movie_default" == "true" ]]; then
    ok "Bazarr subtitle defaults enabled for series + movies"
  else
    warn "Bazarr subtitle defaults enabled for series + movies"
  fi
  if [[ "$baz_single_language" == "false" ]]; then
    ok "Bazarr is configured for multi-language subtitle workflow"
  else
    warn "Bazarr is configured for multi-language subtitle workflow"
  fi
  for provider in opensubtitlescom podnapisi; do
    if echo "$BAZARR_CFG" | grep -Eq "^[[:space:]]+-[[:space:]]*'?$provider'?$"; then
      ok "Bazarr provider enabled: $provider"
    else
      warn "Bazarr provider enabled: $provider"
    fi
  done
fi

check_writable() {
  local deploy="$1"
  local path="$2"
  local label="$3"
  if "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/${deploy}" -- sh -lc "test -w '$path'" >/dev/null 2>&1; then
    ok "$label"
  else
    warn "$label"
  fi
}

echo
ok "Writable path checks"
check_writable "radarr" "/media/movies" "Radarr can write /media/movies"
check_writable "radarr" "/data/torrents/completed/movies" "Radarr can write /data/torrents/completed/movies"
check_writable "sonarr" "/media/tv" "Sonarr can write /media/tv"
check_writable "sonarr" "/data/torrents/completed/tv" "Sonarr can write /data/torrents/completed/tv"
check_writable "lidarr" "/media/music" "Lidarr can write /media/music"
check_writable "readarr" "/media/books" "Readarr can write /media/books"
check_writable "qbittorrent" "/data/torrents/completed/tv" "qBittorrent can write category path /data/torrents/completed/tv"
check_writable "sabnzbd" "/data/usenet/completed" "SABnzbd can write /data/usenet/completed"
check_writable "sabnzbd" "/data/usenet/completed/tv" "SABnzbd can write /data/usenet/completed/tv"
check_writable "sabnzbd" "/data/usenet/completed/movies" "SABnzbd can write /data/usenet/completed/movies"
check_writable "sabnzbd" "/data/usenet/completed/music" "SABnzbd can write /data/usenet/completed/music"
check_writable "sabnzbd" "/data/usenet/completed/books" "SABnzbd can write /data/usenet/completed/books"

check_arr_remote_mapping() {
  local deploy="$1"
  local port="$2"
  local api_base="$3"
  local remote_path="$4"
  local local_path="$5"
  local label="$6"
  local remote_norm="${remote_path%/}"
  local local_norm="${local_path%/}"

  if "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/${deploy}" -- sh -lc \
    "API=\$(grep -o '<ApiKey>[^<]*' /config/config.xml | head -n1 | sed 's#<ApiKey>##'); \
     BODY=\$(curl -fsS -H \"X-Api-Key: \$API\" http://localhost:${port}/api/${api_base}/remotepathmapping 2>/dev/null || true); \
     printf '%s' \"\$BODY\" | tr -d '\n' | grep -E '\"remotePath\"[[:space:]]*:[[:space:]]*\"${remote_norm}/?\"' >/dev/null && \
     printf '%s' \"\$BODY\" | tr -d '\n' | grep -E '\"localPath\"[[:space:]]*:[[:space:]]*\"${local_norm}/?\"' >/dev/null" >/dev/null 2>&1; then
    ok "$label"
  else
    warn "$label"
  fi
}

echo
ok "Arr SAB remote path mapping checks"
check_arr_remote_mapping "sonarr" "8989" "v3" "/config/Downloads/complete" "/data/usenet/completed" "Sonarr has SAB legacy->local remote path mapping"
check_arr_remote_mapping "radarr" "7878" "v3" "/config/Downloads/complete" "/data/usenet/completed" "Radarr has SAB legacy->local remote path mapping"
check_arr_remote_mapping "lidarr" "8686" "v1" "/config/Downloads/complete" "/data/usenet/completed" "Lidarr has SAB legacy->local remote path mapping"
check_arr_remote_mapping "readarr" "8787" "v1" "/config/Downloads/complete" "/data/usenet/completed" "Readarr has SAB legacy->local remote path mapping"

echo
ok "SAB config-as-code checks"
if "${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/sabnzbd -- sh -lc "grep -Eq '^download_dir[[:space:]]*= /data/usenet/incomplete$' /config/sabnzbd.ini"; then
  ok "SAB download_dir defaults to /data/usenet/incomplete"
else
  warn "SAB download_dir defaults to /data/usenet/incomplete"
fi
if "${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/sabnzbd -- sh -lc "grep -Eq '^complete_dir[[:space:]]*= /data/usenet/completed$' /config/sabnzbd.ini"; then
  ok "SAB complete_dir defaults to /data/usenet/completed"
else
  warn "SAB complete_dir defaults to /data/usenet/completed"
fi

echo
echo "Current media file counts from Jellyfin pod mounts"
"${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/jellyfin -- sh -lc '
for d in /media/movies /media/tv /media/music /media/books; do
  c=$(find "$d" -type f 2>/dev/null | wc -l | tr -d " ")
  printf "%s -> %s files\n" "$d" "$c"
done
'

echo
cat <<'EOF'
Interpretation:
- 0 files means the automation wiring can still be healthy, but no media has been imported yet.
- To see content in Jellyfin, request items in Jellyseerr/Arr and wait for download + CDH import.
EOF
