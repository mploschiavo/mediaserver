# Getting Started

Go from zero to streaming in one command. No source code to download.

## Choose Your Platform

You need **one** of these (not both):

| Platform | Install | Works on |
|---|---|---|
| **Docker Compose** (easiest) | [Install Docker](https://docs.docker.com/get-docker/) | Linux, macOS, Windows |
| **Kubernetes** | [Install MicroK8s](https://microk8s.io), [k3s](https://k3s.io), or any cluster | Linux, macOS, Windows (WSL2) |

That's it. No Python, no Git, no source code needed.

---

## Step 1: Deploy

### Option A: Docker Compose (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/mploschiavo/mediaserver/main/dist/docker-compose.yml -o docker-compose.yml
docker compose up -d
```

### Option B: Kubernetes (one command)

```bash
kubectl apply -f https://raw.githubusercontent.com/mploschiavo/mediaserver/main/dist/k8s-deploy.yaml
```

That's it. All 19 services start automatically.

---

## Step 2: Open the Dashboard

The controller service configures everything for you. Watch it work in real time:

**Docker Compose:**
```
http://localhost:9100
```

**Kubernetes:**
```bash
kubectl -n media-stack port-forward svc/media-stack-controller 9100:9100
```
Then open **http://localhost:9100**

The dashboard shows:
- Live progress as each app is configured
- Health status for all services
- API credential status (green = ready)
- Auto-download toggle
- One-click actions (configure, discover indexers, restart)

Bootstrap takes 3-5 minutes. When the status shows **Complete**, everything is wired up.

---

## Step 3: Set Up DNS

After bootstrap completes, the dashboard shows your service URLs. To access them by name, add these to your hosts file:

**Linux / macOS:** `/etc/hosts`
**Windows:** `C:\Windows\System32\drivers\etc\hosts`

**Docker Compose (localhost):**
```
127.0.0.1  apps.media-stack.local jellyfin.media-stack.local homepage.media-stack.local
```

**Kubernetes (replace with your node IP):**
```
192.168.1.60  apps.media-stack.local jellyfin.media-stack.local homepage.media-stack.local
```

Find your node IP:
```bash
hostname -I | awk '{print $1}'    # Linux
ipconfig getifaddr en0            # macOS
```

### Verify your links work

After setting DNS, these should load in your browser:

| Service | URL |
|---|---|
| **Homepage** (start here) | http://apps.media-stack.local/app/homepage |
| **Jellyfin** (watch stuff) | http://jellyfin.media-stack.local |
| **Jellyseerr** (request stuff) | http://apps.media-stack.local/app/jellyseerr |

---

## Step 4: Request Your First Movie

1. Open **Jellyseerr** from the Homepage
2. Sign in with Jellyfin credentials (default: `admin` / `media-stack`)
3. Search for a movie (e.g., "Inception")
4. Click **Request**

Jellyseerr sends the request to Radarr, which searches via Prowlarr's indexers and sends the download to qBittorrent. When complete, the movie appears in Jellyfin.

**First request not working?** Open the controller dashboard and click **Discover Indexers** to add search sources. Or toggle **Auto-Downloads** on.

---

## Step 5: Connect Your TV

Jellyfin works on every device. Use your server's network IP (not localhost):

| Device | How |
|---|---|
| **Samsung TV** | Apps > search "Jellyfin" > install > connect to `http://<YOUR_IP>:8096` |
| **LG TV** | LG Content Store > "Jellyfin" > install |
| **Roku** | Channel Store > "Jellyfin" |
| **Apple TV / iPhone / iPad** | App Store > "Jellyfin" |
| **Android TV / Fire TV** | Play Store > "Jellyfin for Android TV" |
| **Any browser** | Open `http://<YOUR_IP>:8096` |

---

## Step 6: Enable Auto-Downloads (optional)

The default profile starts in **manual mode**. To enable automatic content discovery:

**From the dashboard:** Toggle "Auto-Downloads" to ON, then click "Configure All Apps"

**From the command line:**
```bash
curl -X POST http://localhost:9100/config \
  -H "Content-Type: application/json" \
  -d '{"auto_download_content": true}'
curl -X POST http://localhost:9100/actions/bootstrap
```

This enables:
- Prowlarr auto-discovers and tests indexers
- Sonarr/Radarr discovery lists trigger searches
- Jellyseerr allows automatic request approval

To turn it off, toggle the switch or:
```bash
curl -X POST http://localhost:9100/config \
  -H "Content-Type: application/json" \
  -d '{"auto_download_content": false}'
```

---

## What's Included

19 services, fully configured:

| Service | What it does |
|---|---|
| **Jellyfin** | Media server — watch your content |
| **Jellyseerr** | Request movies and shows |
| **Sonarr** | TV show automation |
| **Radarr** | Movie automation |
| **Lidarr** | Music automation |
| **Readarr** | Book/audiobook automation |
| **Prowlarr** | Indexer management for all Arr apps |
| **qBittorrent** | Torrent downloads |
| **SABnzbd** | Usenet downloads |
| **Bazarr** | Subtitle automation |
| **Envoy** | Gateway proxy (all routing) |
| **Homepage** | Dashboard with links to everything |
| **Tautulli** | Media analytics |
| **Maintainerr** | Library retention policies |
| **Unpackerr** | Auto-extract downloads |
| **FlareSolverr** | Indexer proxy for protected sites |
| **Controller** | Stack config API + dashboard on :9100 |
| **Plex** | Optional alternate media server |
| **Recyclarr** | Quality profile sync |

---

## Common Operations

All from the controller dashboard at http://localhost:9100, or via API:

```bash
# Check status
curl http://localhost:9100/status

# Re-configure all apps (idempotent)
curl -X POST http://localhost:9100/actions/bootstrap

# Discover indexers
curl -X POST http://localhost:9100/actions/auto-indexers

# Rebuild gateway routing
curl -X POST http://localhost:9100/actions/envoy-config

# Restart all apps
curl -X POST http://localhost:9100/actions/restart-apps

# Full API documentation
open http://localhost:9100/api/docs
```

---

## Troubleshooting

**Dashboard shows errors?**
Check the live logs on the dashboard, or download them with the "Download Logs" button.

**Can't reach services?**
Check your `/etc/hosts` entries match your server IP. Verify Envoy is running:
```bash
# Compose
docker ps | grep envoy
# K8s
kubectl -n media-stack get pods | grep envoy
```

**Movies not downloading?**
Open the controller dashboard, click "Discover Indexers", then check Prowlarr for working indexers.

See [docs/troubleshooting.md](docs/troubleshooting.md) for more.

---

## Developers

Want to modify the code? See the full [README](README.md) for developer setup, or:

```bash
git clone https://github.com/mploschiavo/mediaserver.git && cd mediaserver
python deploy.py k8s      # or: python deploy.py compose
```

---

## Next Steps

- [Architecture overview](docs/architecture.md)
- [Service guide](docs/service-guide.md)
- [Operations runbook](docs/operations.md)
- [API documentation](http://localhost:9100/api/docs) (when running)

---

**Project Steward**
Matthew Loschiavo | [matthewloschiavo.com](https://matthewloschiavo.com) | [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) | [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
