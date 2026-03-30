# Platform

Productized platform layout scaffold.

Current deployable runtime manifests remain in `k8s/`.
This directory provides a forward-compatible structure for evolving toward stronger base/overlay ownership.

- `base/`: shared platform baseline concepts
- `overlays/`: environment-specific overlays (`dev`, `lab`, `prod`)
