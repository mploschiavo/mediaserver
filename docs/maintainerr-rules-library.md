# Maintainerr Rules Library (Config-as-Code)

Maintainerr policy rules are managed as individual JSON/YAML files and assembled during bootstrap.

## Source of Truth

- Base policy scaffold: `src/media_stack/contracts/maintainerr_policy.json`
- Default rule library:
  - `src/media_stack/contracts/maintainerr_rules/json/*.json`
  - `src/media_stack/contracts/maintainerr_rules/yaml/*.{yaml,yml}`
- Rendered policy artifact (cluster): `/srv-config/maintainerr/policy.json`

The bootstrap step `ensure_maintainerr_policy` composes the final policy in this order:

1. Base policy defaults
2. Rule files from default library (`src/media_stack/contracts/maintainerr_rules/**`)
3. Optional namespace/local overrides (`maintainerr.rules_library.relative_path`)
4. Inline `maintainerr.policy` overrides from bootstrap JSON

## Rule File Format

Preferred format is **Maintainerr API-shaped rule payload** (same keys used by `POST /api/rules`), with optional wrapper metadata for file-level toggles.

Supported file shapes:

1. Direct API rule object
2. Wrapped object with `enabled` + `rule` (or `payload`)
3. Array of either of the above
4. Native Maintainerr YAML export (`mediaType` + `rules` sections)

Recommended wrapper:

```json
{
  "id": "movies-delete-watched-after-30d",
  "enabled": true,
  "rule": {
    "name": "Delete Watched Movies After 30 Days",
    "description": "Delete watched movies older than 30 days.",
    "library_titles": ["Movies"],
    "dataType": "movie",
    "isActive": true,
    "arrAction": 0,
    "useRules": true,
    "listExclusions": false,
    "forceSeerr": false,
    "notifications": [],
    "rules": [
      {
        "firstVal": [6, 5],
        "operator": null,
        "action": 0,
        "customVal": {"ruleTypeId": 0, "value": "0"},
        "section": 0
      },
      {
        "firstVal": [6, 0],
        "operator": 0,
        "action": 5,
        "customVal": {"ruleTypeId": 1, "value": "days_ago:30"},
        "section": 0
      }
    ],
    "collection": {
      "visibleOnHome": false,
      "visibleOnRecommended": false,
      "keepLogsForMonths": 3
    }
  }
}
```

Notes:

- `library_titles` is a portability helper so rules remain copy/paste friendly across environments where `libraryId` differs.
- `customVal.value` supports relative time tokens: `days_ago:<n>`, `{{days_ago:<n>}}`, `now-<n>d`.
- API export objects containing rule entries with `ruleJson` are also accepted and normalized during sync.
- Legacy `conditions` + `actions` rules are still supported for backward compatibility.
- `.yaml/.yml` files are supported directly in the rules library (including nested subdirectories).
  For YAML exports from Maintainerr UI, the bootstrap sync calls Maintainerr's `/api/rules/yaml/decode` endpoint to translate them before upsert.

## Included Default Rules

The default JSON rule library includes:

- `json/00-protect-favorites.json`
- `json/10-movies-delete-watched-after-30d.json`
- `json/11-tv-delete-watched-after-30d.json`
- `json/12-music-delete-played-after-30d.json`
- `json/13-books-delete-read-after-30d.json`
- `json/20-remove-old-requested-unwatched-90d.json`
- `json/30-tv-unmonitor-unwatched-180d.json`
- `json/40-leaving-soon-collection-5d.json`

These cover:

- Delete watched content (30+ days)
- Remove old requested unwatched content (90+ days)
- Leaving Soon warning collection (5-day window)
- Unmonitor stale TV series
- Protect favorited items from deletion

## Config Controls

Configure in `contracts/services/maintainerr.yaml` (under `defaults`):

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
- `enabled_files`: optional allowlist by basename or relative path (for example `json/40-leaving-soon-collection-5d.json`)

## Add a New Rule

1. Add file under either:
   - `src/media_stack/contracts/maintainerr_rules/json/<nn>-<name>.json`
   - `src/media_stack/contracts/maintainerr_rules/yaml/<nn>-<name>.yaml`
2. Keep a unique `rule.name` (used for merge/override)
3. Run bootstrap:

```bash
bash bin/bootstrap-all.sh
```

4. Verify rendered policy:

```bash
kubectl -n media-stack exec deploy/maintainerr -- sh -lc 'ls -l /opt/data/policy.json'
kubectl -n media-stack exec deploy/maintainerr -- sh -lc 'python3 - <<\"PY\"\nimport json\np=\"/opt/data/policy.json\"\nobj=json.load(open(p))\nprint(\"rules\", len(obj.get(\"rules\") or []))\nprint(\"sample\", [r.get(\"name\") for r in (obj.get(\"rules\") or [])[:5]])\nPY'
```

## Example Rule Snippets

Example 1: Delete watched TV after 30 days (API-shaped)

```json
{
  "rule": {
    "name": "Delete Watched TV After 30 Days",
    "library_titles": ["TV Shows"],
    "dataType": "show",
    "arrAction": 0,
    "useRules": true,
    "notifications": [],
    "rules": [
      {"firstVal": [6, 17], "operator": null, "action": 0, "customVal": {"ruleTypeId": 0, "value": "0"}, "section": 0},
      {"firstVal": [6, 0], "operator": 0, "action": 5, "customVal": {"ruleTypeId": 1, "value": "days_ago:30"}, "section": 0}
    ]
  }
}
```

Example 2: API export copy/paste

```json
{
  "rule": {
    "name": "Copied From API",
    "libraryId": "f137a2dd21bbc1b99aa5c0f6bf02a805",
    "dataType": "movie",
    "arrAction": 0,
    "useRules": true,
    "notifications": [],
    "rules": [
      {"ruleJson": "{\"firstVal\":[6,5],\"operator\":null,\"action\":0,\"customVal\":{\"ruleTypeId\":0,\"value\":\"0\"},\"section\":0}"}
    ]
  }
}
```

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
