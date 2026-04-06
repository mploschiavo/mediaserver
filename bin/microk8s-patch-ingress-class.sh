#!/usr/bin/env bash
set -euo pipefail

TARGET_CLASS="${1:-public}"
NAMESPACE="${NAMESPACE:-media-stack}"
INGRESS_NAME="${INGRESS_NAME:-media-stack-ingress}"

if [[ "${TARGET_CLASS:-}" == "-h" || "${TARGET_CLASS:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  bin/microk8s-patch-ingress-class.sh [INGRESS_CLASS]

Description:
  Patches media-stack ingress to use a specific ingressClassName.
  Useful on MicroK8s where ingress addon commonly uses class "public".

Environment variables:
  NAMESPACE    (default: media-stack)
  INGRESS_NAME (default: media-stack-ingress)
EOF
  exit 0
fi

err() {
  printf '[ERR] %s\n' "$*" >&2
  exit 1
}

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  err "Neither 'microk8s' nor 'kubectl' is available in PATH."
fi

"${KUBECTL[@]}" get ingressclass >/dev/null

"${KUBECTL[@]}" -n "$NAMESPACE" patch ingress "$INGRESS_NAME" \
  --type merge \
  -p "{\"spec\":{\"ingressClassName\":\"$TARGET_CLASS\"}}"

current="$("${KUBECTL[@]}" -n "$NAMESPACE" get ingress "$INGRESS_NAME" -o jsonpath='{.spec.ingressClassName}')"
printf 'Patched ingress %s/%s to ingressClassName=%s\n' "$NAMESPACE" "$INGRESS_NAME" "$current"
