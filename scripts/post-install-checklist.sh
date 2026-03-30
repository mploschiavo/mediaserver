#!/usr/bin/env bash
cat <<'EOF'
1. Run full automation:
   - bash scripts/install.sh --profile full --node-ip <NODE_IP>
2. In Jellyfin:
   - create admin
   - verify libraries under /media/*
3. In Prowlarr:
   - review indexers and add credentials where required
4. Verify one end-to-end request/import in Jellyseerr
5. Optional hardening:
   - change qBittorrent password and update secret with scripts/set-qbit-secret.sh
6. Create baseline backup:
   - bash scripts/backup-stack.sh
EOF
