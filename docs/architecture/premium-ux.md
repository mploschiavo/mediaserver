# Premium UX Playbook

The goal is a polished, low-friction user experience where discovery and playback feel immediate and consistent.

## OTB UX Automation in This Stack

Configured by bootstrap:
- Jellyfin library creation and tuning (movies, TV, music, books)
- metadata language/country defaults
- metadata and image provider priority
- playback defaults (audio/subtitle memory, subtitle mode, next-episode autoplay)
- native-first Jellyfin home UX defaults (collections view disabled in movies)
- optional curated rail collections (disabled by default, can be enabled in config)
- plugin repository + plugin install requests
- Live TV tuner and guide reconciliation
  (with default XMLTV guide source and refresh-on-bootstrap to reduce empty Guide/Now experiences)

## Why UI Can Still Look Flat

Usually one of these is true:
- media has not been imported yet
- naming hygiene is poor (weak metadata matches)
- sources/indexers are not yielding strong releases
- libraries need additional time/refresh to fetch artwork
- Live TV has channel playlist entries but no matching/loaded guide data yet

## High-Impact Quality Actions

1. Keep Arr quality profile defaults focused (1080p/720p strategy).
2. Keep failed-download handling and auto-redownload enabled.
3. Keep hardlink mode enabled to preserve seeding and reduce duplicate storage.
4. Keep Bazarr subtitle automation enabled for reduced playback friction.
5. Use dedicated TV clients (Android TV/Roku/Apple TV) instead of browser-on-TV.

## Optional Netflix-Style Home Plugin

For a stronger "Netflixy" home experience, evaluate Home Screen Sections:
- https://github.com/IAmParadox27/jellyfin-plugin-home-sections

Notes:
- It is an optional plugin with additional prerequisites and version coupling.
- This stack keeps native Jellyfin home as default for stability and lower drift.
- Enable only after validating plugin/Jellyfin version compatibility for your deployment.

## Refresh/Repair Sequence

```bash
# Linux:
bash bin/bootstrap-all.sh
.venv/bin/python -m media_stack.cli.commands.verify_flow_main <NAMESPACE>

# Any OS:
curl -X POST http://localhost:9100/actions/bootstrap
.venv/bin/python -m media_stack.cli.commands.verify_flow_main <NAMESPACE>
```

Then in Jellyfin:
- confirm library paths exist and are populated
- run library refresh if needed
- verify plugins are installed/active after restart
- for Live TV, verify guide source is present and trigger guide refresh if Guide/Now is empty

## Optional Manual Polish (Top 50 Experience)

- curate top posters and backdrops for high-traffic titles
- review collection artwork and naming consistency
- tune home row order per household preferences

## Product Mindset

Treat UX defaults as part of platform engineering, not a post-install cosmetic task.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
