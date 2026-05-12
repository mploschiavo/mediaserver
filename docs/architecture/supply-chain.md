# Supply-chain security

Every controller image we publish to Harbor gets an **SBOM** (software
bill of materials) plus a **cosign signature** before it leaves a
trusted build. Consumers (CI, K8s, `kubectl apply`, operator laptops)
verify both before running the image.

## Why it matters

- A signature proves the image at a tag was built by the expected
  identity — not a silent registry swap.
- An SBOM enumerates every package in the image. When a CVE drops
  against, say, `openssl`, you can answer "are we running an affected
  version?" in seconds via `syft` / `grype` against the attested SBOM.

Together they make supply-chain compromise far harder to hide.

## Tools

| Tool | Install | Purpose |
|---|---|---|
| [syft](https://github.com/anchore/syft) | `curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \| sh -s -- -b ~/.local/bin` | SBOM generation (SPDX-JSON) |
| [cosign](https://docs.sigstore.dev/cosign/installation/) | `go install github.com/sigstore/cosign/v2/cmd/cosign@latest` | Signature + attestation |
| [grype](https://github.com/anchore/grype) (optional) | same installer pattern as syft | CVE scan against an SBOM |

## Local workflow (with a keyful signing key)

```bash
# One-time setup: create a signing keypair.
cosign generate-key-pair
#  → cosign.key + cosign.pub. Guard cosign.key like any private key.

# After building + pushing an image via bin/build-controller-image.sh:
IMAGE=harbor.iomio.io/public/media-stack-controller:v1.0.65

bin/generate-sbom.sh "$IMAGE"
# → artifacts/sbom/harbor.iomio.io-library-media-stack-controller--v1.0.65.spdx.json

COSIGN_KEY=./cosign.key COSIGN_PASSWORD='…' \
  bin/sign-image.sh --with-sbom
# → image manifest signed + SBOM attested + both pushed to Harbor
```

## CI workflow (keyless, OIDC-backed)

GitHub Actions jobs that have `id-token: write` permission can sign
**without a persistent private key** — cosign fetches a short-lived
cert from Fulcio tied to the job's OIDC identity.

```yaml
jobs:
  release-image:
    permissions:
      id-token: write      # required for keyless cosign
      contents: read
    steps:
      - uses: sigstore/cosign-installer@v3
      - uses: anchore/sbom-action@v0
        with:
          image: ${{ env.IMAGE }}
          format: spdx-json
      - run: |
          COSIGN_EXPERIMENTAL=1 bin/sign-image.sh --with-sbom
```

## Verify before running

Add this as a pre-flight in your deploy pipeline / kubeadm apply /
`docker run` wrapper:

```bash
IMAGE=harbor.iomio.io/public/media-stack-controller:v1.0.65 \
EXPECTED_IDENTITY='https://github.com/<org>/<repo>/.github/workflows/release.yml@refs/heads/main' \
bin/verify-image.sh
# → [OK] Image signature valid
# → [OK] SBOM attestation present and valid
```

Exit code **1** if signature fails / is missing. Wire this into your
deploy script so an unsigned image halts the rollout.

### Keyful verification

```bash
IMAGE=... COSIGN_PUB=./cosign.pub bin/verify-image.sh
```

## Vulnerability scan from the attested SBOM

```bash
grype sbom:artifacts/sbom/<image>.spdx.json
```

Pairs well with the SBOM attestation verification: once an image's
SBOM is signed + trusted, any downstream scan against that SBOM is
evidence for the whole supply chain.

## Threat model

| Attack | Mitigation |
|---|---|
| Attacker with registry write swaps the `:v1.0.65` tag | Signature check fails — verify rejects the new image |
| Silently adds a malicious package to a layer | SBOM attestation is tied to manifest digest; a new layer = new digest = no attestation |
| Forges a signature with a stolen key | Keyless mode sidesteps persistent keys; OIDC identity traces back to the build pipeline |
| Claims an image was built from a different commit | cosign attestations carry build metadata (commit SHA, builder, ref) |

## Integration points

- **K8s admission** — consider Sigstore Policy Controller or Kyverno
  with a `keyless` signature verification policy on the media-stack
  namespace. Anything without a valid signature gets rejected at
  admission time.
- **Compose** — `docker trust` is a lighter alternative for the
  compose path, but has no SBOM story. Running `bin/verify-image.sh`
  from `bin/deploy-stack.sh` before `docker compose up` is the
  pragmatic middle ground.
