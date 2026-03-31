# Compose Guide

## Start core stack
```bash
cd docker
docker compose up -d
```

## Start with optional Plex
```bash
docker compose --profile plex up -d
```

## Start with optional SABnzbd
```bash
docker compose --profile usenet up -d
```

## Start with optional NVIDIA Jellyfin variant
```bash
COMPOSE_PROFILES=nvidia docker compose up -d
```

## Useful commands
```bash
docker compose ps
docker compose logs -f jellyfin
docker compose restart sonarr
```

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
