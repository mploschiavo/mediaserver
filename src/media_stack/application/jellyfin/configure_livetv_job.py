"""Jellyfin Live TV job handler.

This is the handler for the configure-livetv job. It does the EPG merge,
guide-first tuner filtering, and delegates to ensure_jellyfin_livetv.

Registered in contracts/services/jellyfin.yaml as:
  configure-livetv:
    handler: media_stack.services.apps.jellyfin.configure_livetv_job:configure_livetv
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import media_stack.services.runtime_platform as runtime_platform


class JellyfinConfigureLiveTvJob:

    def configure_livetv(self, ctx: Any) -> dict[str, Any]:
        """Configure Live TV tuners and guide sources.

        Pre-merges all EPG guides into a single XMLTV file with channel IDs
        rewritten to match M3U tvg-ids. This gives near-100% guide coverage
        and avoids Jellyfin's issues with multiple overlapping guide providers.
        """
        ms_id = ctx.media_server_id()
        if not ms_id:
            return {"skipped": "no media server configured"}

        livetv_key = f"{ms_id}_livetv"
        livetv = ctx.cfg.get(livetv_key, {})
        tuners = livetv.get("tuners", [])
        guides = livetv.get("guides", [])

        if not tuners:
            return {"skipped": "no tuners configured"}

        # Step 1: Pre-merge EPG guides into one file
        if guides:
            try:
                epg_mod = importlib.import_module(f"media_stack.services.apps.{ms_id}.epg_merge_service")
                merge_epgs = epg_mod.merge_epgs

                # Collect M3U paths (materialized files on disk)
                m3u_paths = []
                for t in tuners:
                    mat = t.get("materialized_output_path", "")
                    if mat:
                        m3u_paths.append(str(Path(ctx.config_root) / mat))

                # Build EPG source list from guides
                epg_sources = []
                for g in guides:
                    path = g.get("path", "")
                    name = g.get("name", path[:40])
                    if path and (path.startswith("http://") or path.startswith("https://")):
                        epg_sources.append({"url": path, "name": name})

                if m3u_paths and epg_sources:
                    merged_path = str(Path(ctx.config_root) / ms_id / "livetv-guides" / "merged-epg.xml")
                    result = merge_epgs(
                        m3u_paths=m3u_paths,
                        epg_sources=epg_sources,
                        output_path=merged_path,
                        config_root=ctx.config_root,
                        log=runtime_platform.log,
                    )

                    if result.get("channels_with_programmes", 0) > 0:
                        container_path = "/config/livetv-guides/merged-epg.xml"
                        # Carry forward enrichment flags from the original guide configs
                        # so the downstream handler enriches programmes with channel logos.
                        enrich_icons = any(
                            g.get("enrich_program_icons_from_tuner_logo", False) for g in guides
                        )
                        enrich_categories = any(
                            g.get("enrich_program_categories_from_tuner_groups", False) for g in guides
                        )
                        merged_guide: dict[str, Any] = {
                            "type": "xmltv",
                            "path": container_path,
                            "materialized_output_path": f"{ms_id}/livetv-guides/merged-epg.xml",
                            "enable_all_tuners": True,
                            "enrich_program_icons_from_tuner_logo": enrich_icons,
                            "enrich_program_categories_from_tuner_groups": enrich_categories,
                        }
                        # Carry forward icon/category config from the first guide that has them
                        for g in guides:
                            for key in (
                                "default_program_icon_url",
                                "replace_existing_program_icons_with_tuner_logo",
                                "movie_categories", "sports_categories",
                                "kids_categories", "news_categories",
                            ):
                                if key in g and key not in merged_guide:
                                    merged_guide[key] = g[key]
                        livetv["guides"] = [merged_guide]
                        runtime_platform.log(
                            f"[OK] Live TV: using merged EPG ({result['channels_with_programmes']} "
                            f"channels, {result['programmes']} programmes)"
                        )
            except Exception as exc:
                runtime_platform.log(f"[WARN] Live TV: EPG merge failed ({exc}), falling back to individual guides")

        # Step 2: Run the livetv handler
        # Ensure API key is set
        from media_stack.api.services.registry import SERVICE_MAP, read_api_key_from_file, read_api_key_via_http
        import os
        svc = SERVICE_MAP.get(ms_id)
        if svc and svc.api_key_env and not os.environ.get(svc.api_key_env):
            discovered = read_api_key_from_file(ms_id, ctx.config_root)
            if not discovered:
                try:
                    discovered = read_api_key_via_http(ms_id)
                except Exception as exc:
                    runtime_platform.log(f"[DEBUG] {ms_id}: HTTP API key discovery failed: {exc}")
            if discovered:
                os.environ[svc.api_key_env] = discovered

        if not ctx.media_server_api_key():
            return {"skipped": f"no API key for {ms_id}"}

        try:
            mod = importlib.import_module(f"media_stack.services.apps.{ms_id}.runtime_ops")
            fn = getattr(mod, f"ensure_{ms_id}_livetv", None)
            if fn:
                fn(ctx.cfg, ctx.config_root, ctx.wait_timeout)
                return {"service": ms_id}
            return {"skipped": f"no livetv handler for {ms_id}"}
        except Exception as exc:
            raise RuntimeError(f"Live TV configuration failed: {exc}") from exc


_instance = JellyfinConfigureLiveTvJob()
configure_livetv = _instance.configure_livetv
