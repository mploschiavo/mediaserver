#!/usr/bin/env bash
echo "[WARN] scripts/post-install-checklist.sh is deprecated."
echo "[WARN] Use docs/first-run-wiring.md and docs/operations.md for the current runbooks."

cat <<'EOF'
1. Run full automation:
   - bash scripts/install.sh --profile full --node-ip <NODE_IP>
2. Verify Jellyfin bootstrap and API key:
   - bash scripts/ensure-jellyfin-bootstrap.sh
   - verify libraries under /media/*
3. In Prowlarr:
   - review indexers and add credentials where required
4. Verify one end-to-end request/import in Jellyseerr
5. Optional hardening:
   - change qBittorrent password and update secret with scripts/set-qbit-secret.sh
6. Create baseline backup:
   - bash scripts/backup-stack.sh
EOF
