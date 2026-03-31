# Maintainerr Rules Library (Config-as-Code)

Maintainerr policy rules are now managed as individual JSON files and assembled during bootstrap.

## Source of Truth

- Base policy scaffold: `scripts/bootstrap_defaults/maintainerr_policy.json`
- Default rule library: `scripts/bootstrap_defaults/maintainerr_rules/*.json`
- Rendered policy artifact (cluster): `/srv-config/maintainerr/policy.json`

The bootstrap step `ensure_maintainerr_policy` composes the final policy in this order:

1. Base policy defaults
2. Rule files from default library (`scripts/bootstrap_defaults/maintainerr_rules`)
3. Optional namespace/local overrides (`maintainerr.rules_library.relative_path`)
4. Inline `maintainerr.policy` overrides from bootstrap JSON

## Rule File Format

Each file can be either:

1. A direct rule object
2. A wrapped object with `enabled` + `rule`

Recommended (wrapper format):

```json
{
  "id": "movies-delete-watched-after-30d",
  "enabled": true,
  "description": "Delete watched movies older than 30 days.",
  "rule": {
    "name": "Delete Watched Movies After 30 Days",
    "libraries": ["Movies"],
    "conditions": {"watched": true, "added_days_ago_gte": 30},
    "actions": {"delete_item": true, "arr_delete_or_unmonitor": "delete"}
  }
}
```

## Included Default Rules

The default library includes:

- `00-protect-favorites.json`
- `10-movies-delete-watched-after-30d.json`
- `11-tv-delete-watched-after-30d.json`
- `12-music-delete-played-after-30d.json`
- `13-books-delete-read-after-30d.json`
- `20-remove-old-requested-unwatched-90d.json`
- `30-tv-unmonitor-unwatched-180d.json`
- `40-leaving-soon-collection-5d.json`

These cover:

- Delete watched content (30+ days)
- Remove old requested unwatched content (90+ days)
- Leaving Soon warning collection (5-day window)
- Unmonitor stale TV series
- Protect favorited items from deletion

## Config Controls

Configure in `bootstrap/media-stack.bootstrap.json`:

```json
"maintainerr": {
  "rules_library": {
    "enabled": true,
    "include_defaults": true,
    "relative_path": "maintainerr/rules",
    "merge_mode": "append",
    "enabled_files": []
  }
}
```

Behavior:

- `merge_mode: append`: custom rules override same-name defaults and add new ones
- `merge_mode: replace`: only custom library rules are used (if present)
- `enabled_files`: optional filename allowlist

## Add a New Rule

1. Add file under `scripts/bootstrap_defaults/maintainerr_rules/<nn>-<name>.json`
2. Keep a unique `rule.name` (used for merge/override)
3. Run bootstrap:

```bash
bash scripts/bootstrap-all.sh
```

4. Verify rendered policy:

```bash
kubectl -n media-stack exec deploy/maintainerr -- sh -lc 'ls -l /opt/data/policy.json'
kubectl -n media-stack exec deploy/maintainerr -- sh -lc 'python3 - <<\"PY\"\nimport json\np=\"/opt/data/policy.json\"\nobj=json.load(open(p))\nprint(\"rules\", len(obj.get(\"rules\") or []))\nprint(\"sample\", [r.get(\"name\") for r in (obj.get(\"rules\") or [])[:5]])\nPY'
```

## Example Rule Snippets

Example 1: Protect favorites

```json
{"rule":{"name":"Protect Favorited Media","conditions":{"favorited_by_any_user":true},"actions":{"protect_item":true}}}
```

Example 2: Delete watched TV after 30 days

```json
{"rule":{"name":"Delete Watched TV After 30 Days","libraries":["TV Shows"],"conditions":{"watched":true,"added_days_ago_gte":30},"actions":{"delete_item":true}}}
```

Example 3: Remove old requested unwatched content

```json
{"rule":{"name":"Remove Old Requested Unwatched Content","conditions":{"requested_via":"jellyseerr","watched":false,"requested_days_ago_gte":90},"actions":{"delete_item":true,"remove_request_record":true}}}
```

Example 4: Unmonitor stale TV

```json
{"rule":{"name":"Unmonitor Unwatched TV After 180 Days","libraries":["TV Shows"],"conditions":{"watched":false,"last_watched_days_ago_gte":180},"actions":{"arr_unmonitor":true}}}
```

Example 5: Leaving Soon collection

```json
{"rule":{"name":"Leaving Soon (5 Day Warning)","actions":{"add_to_collection":"Leaving Soon","collection_days_before_delete":5}}}
```
