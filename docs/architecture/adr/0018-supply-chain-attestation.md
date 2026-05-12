# ADR-0018 — Supply-chain attestation for published images

**Status:** Proposed (2026-05-12). Filed during the v1.0.368
release-prep pass when checklist items 4–6 (sign images / generate
SBOM / verify image) surfaced as the remaining gap between "image
published to harbor" and "operator can trust the image".

Authors: matthew

## Context

The controller + UI images are published to
`harbor.iomio.io/public/media-stack-{controller,ui}:vX.Y.Z` by the
release pipeline (`media-stack-release` console-script, see
ADR-0015 Phase 7f). The pipeline today:

* Computes the version from `VERSION` / `VERSION-UI`.
* Builds both images.
* Pushes `:latest` + `:vX.Y.Z` tags.
* Records the digest in `dist/release-metadata.json` for the
  deploy-CLI to consume (ADR-0016).

What's missing — the layer the
2026-05-12 secret-leak incident response made operator-visible:

* **No signature.** An operator pulling
  `harbor.iomio.io/public/media-stack-controller:v1.0.368` has no
  cryptographic proof that the image was built by us. A compromised
  registry credential is sufficient to push a malicious image under
  the same tag; consumers would pull it transparently.
* **No SBOM.** A CVE drops against (say) `urllib3 < 2.1.1` and an
  operator has no fast way to answer "is my deployed image
  affected?" short of `docker run … pip freeze` or re-reading the
  Dockerfile. Same applies to UI npm deps.
* **No reproducible-verify path.** An operator who *does* want to
  paranoid-check has to read the Dockerfile, build locally, and
  byte-compare layers — friction high enough that nobody does it.

This is also the standard expectation for any image landing on
GitHub Packages, the OpenSSF Best Practices badge, and most
SBOM-aware vulnerability tooling (Trivy, Grype, Snyk).

## Decision

Adopt the cosign + SLSA-style attestation pattern. Three concrete
deliverables, each independently usable:

### Phase 1 — Sign published images with cosign keyless

* Use `cosign sign --yes` with the GitHub OIDC keyless flow
  (Sigstore Fulcio root). No key management on operator side;
  identity is the GitHub Actions workflow that built the image,
  signed by the public-good Fulcio CA.
* The cosign signature lands as a sibling reference in the same
  Harbor registry — `harbor.iomio.io/public/media-stack-controller:sha256-<digest>.sig`.
  Harbor's OCI distribution v2 spec already supports this.
* Verification one-liner published in README:
  ```
  cosign verify harbor.iomio.io/public/media-stack-controller:v1.0.368 \
    --certificate-identity-regexp '.*mediaserver/.github/workflows/release.*' \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com
  ```
* CI hook: the `media-stack-release` pipeline gains a post-build
  `sign` step that runs only when `GITHUB_ACTIONS=true` (so local
  builds don't pretend to sign).

### Phase 2 — Generate + attach SBOMs

* Use `syft packages docker:harbor.iomio.io/public/media-stack-controller:vX.Y.Z`
  to emit a CycloneDX SBOM per image.
* Attach to the image via `cosign attest --type cyclonedx --predicate sbom.cdx.json`.
* SBOM is consumable by Trivy / Grype / Dependency-Track for
  passive CVE matching. The release notes from v1.0.369+ will link
  the SBOM hash so operators can sanity-check after pulling.
* Per-image: one for controller (Python + base image deps), one
  for UI (npm + nginx base). The two trees are independent.

### Phase 3 — Verification utility shipped in-tree

* Add `bin/test/verify-image-attestation.sh` that runs the cosign
  verify + SBOM extract for both images at the deployed tag,
  fails non-zero if either check fails. Wire it into the live test
  harness (`bin/test/`) so the in-cluster verifier can refuse to
  declare success on an unsigned image.
* Add a ratchet
  (`tests/unit/ratchets/test_release_attestation_ratchet.py`) that
  every published version listed in CHANGELOG has both a signature
  and an SBOM, fetched from the registry at test time. The ratchet
  has a `baseline` for pre-Phase-1 versions so the historical tail
  is grandfathered.

### Phase 4 — Provenance attestations (SLSA Level 3)

* `cosign attest --type slsaprovenance` with the build provenance
  emitted by the GitHub Actions runner.
* SLSA v1.0 schema: `_type, predicateType, subject, builder,
  buildType, invocation, metadata`. The `buildType` is the
  `media-stack-release` action; `invocation.configSource` carries
  the commit SHA the build came from. Closes the loop on "show me
  the source for this image".
* This is the highest-friction phase — GitHub Actions hardening +
  reusable workflow patterns + a separate verifier
  (`slsa-verifier`) on the operator side. Deferred until Phase 1–3
  shake out.

## Phases

| Phase | Status | Notes |
|---|---|---|
| Phase 1 — cosign keyless signing of controller + UI images | not started | The 90% fix. Sigstore handles key management; only the release workflow + README one-liner are new code. |
| Phase 2 — syft SBOM generation + cosign attest --type cyclonedx | not started | Pairs naturally with Phase 1. SBOM tooling is mature; the integration is a workflow step + a documentation note. |
| Phase 3 — `verify-image-attestation.sh` + ratchet against CHANGELOG | not started | Closes the loop in-cluster: post-Phase-3, an unsigned image can't pass the verifier. |
| Phase 4 — SLSA provenance attestations | not started | Highest scope. Wait for Phases 1–3 to settle and operator feedback before committing. |

## What this ADR does NOT propose

* **A private signing key.** Cosign keyless via Sigstore is the
  default; key-pair signing is a workaround for environments
  without OIDC. We have GitHub Actions OIDC; use it.
* **A vendor-locked SBOM format.** CycloneDX is the format because
  it's both Trivy-friendly and the format CISA's SBOM minimum
  elements doc references. SPDX would also work but most of our
  consumer-side tooling assumes CycloneDX.
* **Mandatory signature verification for `:latest`.** Tag pulls
  during dev / smoke-test stay unsigned-pullable. The verifier
  ratchet only blocks the release path.
* **Re-implementing the cosign / syft toolchain.** Both are stable
  upstream projects with binary distributions. The release workflow
  installs them via setup-cosign + setup-syft GitHub Actions; the
  in-cluster verifier expects them on the operator's PATH.

## Cross-references

* `bin/release/regen-dist.sh` — the build-side hook the Phase 1
  signing step extends (see also ADR-0015 Phase 8 — `regen-dist.sh`
  ratchet-path follow-up).
* `media-stack-release` console-script — the pipeline orchestrator
  the sign + SBOM + provenance steps slot into.
* ADR-0017 (`/api/backup` redact-by-default) — a different
  attestation problem (the runtime data plane); this ADR covers
  the build artefacts. The two are independent.
* The 2026-05-12 secret-leak incident write-up in CHANGELOG
  v1.0.368 — drove the broader "what does trust look like for
  this project" thread that surfaced the signing gap.

---

**Project Steward**
Matthew Loschiavo · [matthewloschiavo.com](https://matthewloschiavo.com) · [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com)
