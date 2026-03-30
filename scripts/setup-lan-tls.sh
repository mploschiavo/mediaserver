#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
INGRESS_NAME="${INGRESS_NAME:-media-stack-ingress}"
TLS_SECRET_NAME="${TLS_SECRET_NAME:-media-stack-tls}"
NODE_IP="${NODE_IP:-}"

usage() {
  cat <<'EOF'
Usage:
  scripts/setup-lan-tls.sh

Description:
  Generates a LAN TLS certificate for ingress hosts and configures ingress TLS.
  Prefers mkcert, falls back to openssl self-signed cert.

Environment variables:
  NAMESPACE        (default: media-stack)
  INGRESS_NAME     (default: media-stack-ingress)
  TLS_SECRET_NAME  (default: media-stack-tls)
  NODE_IP          (optional SAN IP)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  echo "[ERR] Neither microk8s nor kubectl is available in PATH." >&2
  exit 1
fi

if [[ -z "$NODE_IP" ]]; then
  NODE_IP="$(hostname -I | awk '{print $1}')"
fi

mapfile -t hosts < <("${KUBECTL[@]}" -n "$NAMESPACE" get ingress "$INGRESS_NAME" -o jsonpath='{range .spec.rules[*]}{.host}{"\n"}{end}' 2>/dev/null)
if [[ "${#hosts[@]}" -eq 0 ]]; then
  echo "[ERR] No ingress hosts found on $NAMESPACE/$INGRESS_NAME" >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
cleanup() { rm -rf "$tmp_dir"; }
trap cleanup EXIT

crt="${tmp_dir}/tls.crt"
key="${tmp_dir}/tls.key"

if command -v mkcert >/dev/null 2>&1; then
  echo "[INFO] Generating TLS cert with mkcert"
  if [[ -n "$NODE_IP" ]]; then
    mkcert -cert-file "$crt" -key-file "$key" "${hosts[@]}" "$NODE_IP"
  else
    mkcert -cert-file "$crt" -key-file "$key" "${hosts[@]}"
  fi
else
  echo "[WARN] mkcert not found; generating self-signed cert with openssl"
  command -v openssl >/dev/null 2>&1 || {
    echo "[ERR] openssl is required when mkcert is not installed." >&2
    exit 1
  }

  san_file="${tmp_dir}/san.cnf"
  {
    echo "[req]"
    echo "distinguished_name=req_distinguished_name"
    echo "x509_extensions=v3_req"
    echo "prompt=no"
    echo "[req_distinguished_name]"
    echo "CN=${hosts[0]}"
    echo "[v3_req]"
    echo "subjectAltName=@alt_names"
    echo "[alt_names]"
    i=1
    for h in "${hosts[@]}"; do
      echo "DNS.${i}=${h}"
      i=$((i + 1))
    done
    if [[ -n "$NODE_IP" ]]; then
      echo "IP.1=${NODE_IP}"
    fi
  } >"$san_file"

  openssl req -x509 -nodes -newkey rsa:4096 \
    -keyout "$key" -out "$crt" -days 365 \
    -config "$san_file" -extensions v3_req >/dev/null 2>&1
fi

"${KUBECTL[@]}" -n "$NAMESPACE" create secret tls "$TLS_SECRET_NAME" \
  --cert="$crt" --key="$key" \
  --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f -

hosts_json=""
for h in "${hosts[@]}"; do
  [[ -n "$hosts_json" ]] && hosts_json+=", "
  hosts_json+="\"$h\""
done

"${KUBECTL[@]}" -n "$NAMESPACE" patch ingress "$INGRESS_NAME" --type merge \
  -p "{\"spec\":{\"tls\":[{\"secretName\":\"${TLS_SECRET_NAME}\",\"hosts\":[${hosts_json}]}]}}" >/dev/null

echo "[OK] TLS secret applied: ${NAMESPACE}/${TLS_SECRET_NAME}"
echo "[OK] Ingress TLS enabled on ${NAMESPACE}/${INGRESS_NAME}"
