"""Core operations — bootstrap-pipeline jobs that aren't tied to a single
managed app (envoy regen, credential validation, post-setup tuning, etc.).

These migrated from the hardcoded ``_CORE_ACTIONS`` table to the
job framework so they appear in the Job tree and benefit from
prereq gating. See ``job_adapters`` for the JobContext wrappers
and ``contracts/services/core.yaml`` for the job declarations."""
