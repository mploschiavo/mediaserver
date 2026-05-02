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

### Option A: Docker Compose

Create a directory and deploy (works on Linux, macOS, and Windows):

```bash
# Linux / macOS
mkdir -p ~/media-stack && cd ~/media-stack
curl -fsSL https://raw.githubusercontent.com/mploschiavo/mediaserver/main/dist/docker-compose.yml -o docker-compose.yml
docker compose up -d
```

```powershell
# Windows (PowerShell)
mkdir $HOME\media-stack; cd $HOME\media-stack
Invoke-WebRequest -Uri https://raw.githubusercontent.com/mploschiavo/mediaserver/main/dist/docker-compose.yml -OutFile docker-compose.yml
docker compose up -d
```

> **Important:** Run from a persistent directory (`~/media-stack`, not `/tmp`). Config, media, and download data are stored relative to the compose file. Using `/tmp` on Linux means data is lost on reboot.

### Option B: Kubernetes

Download and apply the K8s manifest:

```bash
curl -fsSL https://raw.githubusercontent.com/mploschiavo/mediaserver/main/dist/k8s-deploy.yaml -o k8s-deploy.yaml
kubectl apply -f k8s-deploy.yaml
```

All 19 services start automatically. An init container creates the config/data/media directories with correct ownership (UID/GID 1000) before services start.

> **Permissions note:** Services like Jellyfin and Maintainerr run as non-root (UID 1000). The compose file includes an `init-permissions` service that automatically sets ownership on first deploy. If you see "Permission denied" errors after upgrading from an older version, run:
> ```bash
> docker run --rm -v ./config:/cfg -v ./data:/data -v ./media:/media alpine chown -R 1000:1000 /cfg /data /media
> ```

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
- Live progress as each app is configured (SSE streaming logs)
- Health status for all 16 services with response times
- API credential validation (authenticated probe per service)
- Download queue, library stats, and disk usage widgets
- Service versions, indexer performance, and quality profiles
- Auto-download toggle (auto-triggers reconcile)
- One-click actions (configure, discover indexers, restart, per-service restart)
- DNS access matrix, service topology map, and container log viewer
- Prometheus metrics at `/metrics`, RSS feed at `/api/feed.xml`
- Full OpenAPI spec at `/api/openapi.json` (40 endpoints)

Bootstrap takes 3-5 minutes. When the status shows **Complete**, everything is wired up.

### Direct Access (Docker Compose)

Every service is accessible on localhost immediately — no DNS setup required:

| Service | URL | Port |
|---|---|---|
| **Controller Dashboard** | http://localhost:9100 | 9100 |
| **Jellyfin** (watch stuff) | http://localhost:8096 | 8096 |
| **Homepage** (start here) | http://localhost:3000 | 3000 |
| **Jellyseerr** (request stuff) | http://localhost:5055 | 5055 |
| **Sonarr** (TV) | http://localhost:8989 | 8989 |
| **Radarr** (Movies) | http://localhost:7878 | 7878 |
| **Prowlarr** (Indexers) | http://localhost:9696 | 9696 |
| **Lidarr** (Music) | http://localhost:8686 | 8686 |
| **Readarr** (Books) | http://localhost:8787 | 8787 |
| **Bazarr** (Subtitles) | http://localhost:6767 | 6767 |
| **qBittorrent** | http://localhost:8080 | 8080 |
| **SABnzbd** | http://localhost:8085 | 8085 |
| **Tautulli** (Analytics) | http://localhost:8181 | 8181 |
| **Maintainerr** | http://localhost:6246 | 6246 |

> **Override ports:** Set environment variables like `JELLYFIN_PORT=9096` to change the host port.

---

## Step 3: Set Up DNS (optional)

For cleaner URLs, you can add DNS entries. This is **optional** — direct port access works immediately.

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

### Verify DNS links work

After setting DNS, these should also work:

| Service | DNS URL |
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

**First request not working?** Open the controller dashboard and click **Discover Indexers** to add search sources. Confirm **Auto-Add Content** is on.

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

## Step 6: Pause Auto-Add Content (optional)

The default profile ships with **Auto-Add Content: on** — import lists, RSS, and scheduled searches automatically queue new content into your download clients. To pause the auto-queue (e.g. disk-full, quiet hours, or you want full manual control):

**From the dashboard:** Toggle **Auto-Add Content** to off — the dashboard runs a reconcile to apply the change. In-progress downloads keep running; only the auto-queue stops.

**From the command line:**
```bash
curl -X POST http://localhost:9100/config \
  -H "Content-Type: application/json" \
  -d '{"auto_download_content": false}'
curl -X POST http://localhost:9100/actions/reconcile
```

To re-enable, flip the toggle or POST `{"auto_download_content": true}`.

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

See [troubleshooting.md](../how-to/troubleshooting.md) for more.

---

## Developers

Want to modify the stack or add services? Clone the repo:

```bash
git clone https://github.com/mploschiavo/mediaserver.git && cd mediaserver
```

- **Add a service:** see [architecture/adding-a-service.md](../architecture/adding-a-service.md)
- **Swap a technology:** Edit `contracts/media-stack.profile.yaml` → `technology_bindings` (details in [architecture/technology-swaps.md](../architecture/technology-swaps.md))
- **Full developer guide:** [architecture/principles.md](../architecture/principles.md), [architecture/overview.md](../architecture/overview.md), [reference/configuration.md](../reference/configuration.md)

---

## Next Steps

- [Architecture overview](../architecture/overview.md)
- [Service catalog](../reference/service-catalog.md)
- [Operations runbook](../how-to/operations.md)
- [API documentation](http://localhost:9100/api/docs) (when running)

---

**Project Steward**
Matthew Loschiavo | [matthewloschiavo.com](https://matthewloschiavo.com) | [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) | [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
