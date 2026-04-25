# Contributing

Thanks for being here. Media Stack is small enough that the process is light.

## Reporting a bug

Use the [bug-report template](.github/ISSUE_TEMPLATE/bug_report.yml). Skim the [troubleshooting guide](docs/how-to/troubleshooting.md) and search [existing issues](../../issues) first.

What helps a maintainer fix your bug fast:

- Steps that reproduce on a fresh install (`compose down -v` + wipe `config/`).
- The exact controller image tag (e.g. `v1.0.155`).
- Controller logs (`docker logs media-stack-controller --tail 200`).
- The relevant excerpt of your bootstrap profile, with **secrets redacted**.

What slows things down: "It doesn't work" with no version, no logs, and no reproduction steps.

## Reporting a security issue

**Don't open a public issue.** Use [GitHub Security Advisories](https://github.com/mploschiavo/mediaserver/security/advisories/new) so the fix can ship before the vulnerability is public.

## Suggesting a feature

[Feature template](.github/ISSUE_TEMPLATE/feature_request.yml). Lead with the **problem** you want solved, not the solution. The more specific the friction, the better the conversation.

## Asking a question

[Question template](.github/ISSUE_TEMPLATE/question.yml). Check the [docs index](docs/README.md) and the dashboard's Routes / Status / Services tabs first — most operational questions have an answer there.

---

## Development setup

See [docs/how-to/deployment.md → Prerequisites — developer](docs/how-to/deployment.md#prerequisites--developer) for the full toolchain. Short version:

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install --upgrade pip docker kubernetes pyyaml requests ruff black
bash bin/test.sh
```

## Branch flow

- `main` is shippable. Releases are tagged from `main`.
- Branch naming: `fix/<short-desc>` for bugs, `feat/<short-desc>` for features, `docs/<short-desc>` for documentation.
- Keep PRs focused — one concern per PR. If you find unrelated cleanup along the way, ship it as a follow-up PR.

## PR process

1. Open a draft PR early if you want directional feedback.
2. Use the [PR template](.github/PULL_REQUEST_TEMPLATE.md). Fill in the test-plan section with what you actually verified, not what you intend to verify.
3. CI must pass — unit tests, the meta-ratchet (`tests/unit/test_promises_registry.py`), security harness, and the linter.
4. If your PR changes a behavior covered by [`contracts/promises.yaml`](contracts/promises.yaml), update the promise. If you add a new OTB guarantee, add a new promise and re-run `python3 bin/render-promises-reference.py`.
5. Maintainer reviews, may ask for changes, then merges (no force-push to other people's branches).

## Adding a service

See [docs/architecture/adding-a-service.md](docs/architecture/adding-a-service.md) (forthcoming) or follow the pattern in `contracts/services/` + `src/media_stack/services/apps/`. Two-location rule: the service contract lives in `contracts/services/<name>.yaml`, the implementation lives in `src/media_stack/services/apps/<name>/`. Zero platform code changes.

## Coding conventions

- Python: `ruff` + `black` (run via `bash bin/test.sh`).
- No `@staticmethod`; constructor-injected dependencies; no module-level singletons.
- No defensive try/except around code paths that should always succeed — let it crash and fix the root cause.
- Comment only when the WHY isn't obvious from the code.
- User-facing strings carry no developer-speak (no "ext_authz", "kustomize overlay", "OIDC scope leak"). The dashboard is a doc surface for end users.

## Releases

Maintainer-only. Bump the patch with:

```bash
bash bin/release.sh 1.0.X
```

This builds, pushes the controller image to harbor, and updates `dist/docker-compose.yml` + `dist/k8s-deploy.yaml` + `docker/docker-compose.yml` to the new tag.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
