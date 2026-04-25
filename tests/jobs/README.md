# tests/jobs — per-job contract tests

The job framework runs a fixed set of named jobs (`discover-api-keys`,
`post-setup`, `configure-libraries`, ...). Each job has the same
four failure modes — and historically each test author covered a
different two of them, leaving asymmetric coverage that the
2026-04 `discover-api-keys` regression slipped through.

This directory is the canonical home for one file per job. The
shape of every file is identical; the generator at
`media-stack-scaffold-job-test` produces it.

## The four scenarios every job must cover

1. **Success path.** The happy run: every prerequisite present,
   the job's documented side effects are observed (config files
   written, env populated, K8s patch fired, etc.). Asserts the
   job returns `status: "ok"` (or its documented success shape).

2. **Service-unreachable path.** The upstream service the job
   talks to is down. The job must **not** raise — it should
   return a structured error (`status: "error"` with an error
   string, or `status: "skipped"` if the contract permits skipping).

3. **Partial-skip path.** Some sub-tasks succeed, some are
   skipped because a prerequisite isn't ready (e.g. three of
   five `*arr`s have keys, two don't). The job must remain
   stable and report which sub-tasks ran.

4. **Idempotency.** Running the job twice is observationally
   identical to running it once. Re-runs must be cheap and safe.
   This is the ratchet that catches "we wrote an empty value
   over a populated key on the second pass" bugs.

## Generating a new test

```bash
media-stack-scaffold-job-test <job-name>
```

The generator emits `tests/jobs/test_<job-name>.py` with one
`TestCase` per scenario, each calling a `_todo()` placeholder.
Replace the placeholders with real assertions backed by the
job's fixture set.

The deliberate choice to ship the scaffold with `skipTest`
calls (rather than `pass`) means the TODO surfaces every time
pytest runs — silent skips are visible in the test summary and
serve as a soft ratchet against half-implemented coverage.

## Existing files in this directory

- `test_discover_api_keys_secret_writeback.py` — the bootstrap
  → K8s Secret round-trip (ratchet #4).

When you add a new job to the framework, run the scaffolder and
fill in the four scenarios. New PRs that introduce a job without
its `tests/jobs/test_<name>.py` companion should be flagged in
review.
