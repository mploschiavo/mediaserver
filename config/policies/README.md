# Config Policies

Reserved for policy-level configuration domains (quality, retention, automation guardrails).

Current organization:
- `src/media_stack/contracts/maintainerr_policy.json`: base Maintainerr policy scaffold.
- `src/media_stack/contracts/maintainerr_rules/json/`: canonical API-shaped rule files.
- `src/media_stack/contracts/maintainerr_rules/yaml/`: optional Maintainerr UI export YAML rules.

Source-control guidance:
- keep one rule per file with stable `rule.name` values for clean merge/override behavior.
- prefer JSON for canonical, reviewable defaults; use YAML for imported/exported rule portability.
- keep environment/team overrides under `maintainerr.rules_library.relative_path` instead of editing defaults directly.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
