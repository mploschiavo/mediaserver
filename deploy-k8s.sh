#!/usr/bin/env bash
# Deploy media stack to Kubernetes — single command.
#
# Usage:
#   ./deploy-k8s.sh                                              # default profile
#   ./deploy-k8s.sh examples/bootstrap-profiles/media-k8s-standard.yaml
#   ./deploy-k8s.sh my-profile.yaml --delete  # teardown + redeploy

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_FILE="${1:-examples/bootstrap-profiles/media-k8s-standard.yaml}"

if [[ "$PROFILE_FILE" == *.yaml ]] || [[ "$PROFILE_FILE" == *.yml ]]; then
    shift || true
else
    PROFILE_FILE="examples/bootstrap-profiles/media-k8s-standard.yaml"
fi

PROFILE_PATH="$SCRIPT_DIR/$PROFILE_FILE"
[[ -f "$PROFILE_PATH" ]] || { echo "ERROR: Profile not found: $PROFILE_PATH" >&2; exit 1; }

# Extract values from profile YAML.
_yaml_val() { grep "$1:" "$PROFILE_PATH" | head -1 | sed "s/.*$1:\s*//" | tr -d '"' | tr -d "'" | xargs; }

NAMESPACE=$(_yaml_val "name")
INSTALL_PROFILE=$(_yaml_val "install_profile")

[[ -n "$NAMESPACE" ]] || { echo "ERROR: metadata.name is required in profile" >&2; exit 1; }

echo "K8s deploy: namespace=$NAMESPACE profile=${INSTALL_PROFILE:-standard}"

# Handle --delete flag.
if [[ "${1:-}" == "--delete" ]]; then
    echo "  Deleting namespace $NAMESPACE..."
    kubectl delete namespace "$NAMESPACE" --force --grace-period=0 2>/dev/null || true
    for i in $(seq 1 24); do kubectl get ns "$NAMESPACE" 2>&1 | grep -q "NotFound" && break; sleep 5; done
    shift || true
fi

# Resolve kustomize profile directory.
PROFILE_DIR="$SCRIPT_DIR/k8s/profiles/${INSTALL_PROFILE:-standard}"
[[ -d "$PROFILE_DIR" ]] || { echo "ERROR: K8s profile dir not found: $PROFILE_DIR" >&2; exit 1; }

# Generate kustomization with namespace override.
KUST_BACKUP="$PROFILE_DIR/kustomization.yaml.bak"
cp "$PROFILE_DIR/kustomization.yaml" "$KUST_BACKUP"
trap "mv '$KUST_BACKUP' '$PROFILE_DIR/kustomization.yaml' 2>/dev/null || true" EXIT

# Add namespace field to kustomization.
python3 -c "
import yaml, sys
with open('$PROFILE_DIR/kustomization.yaml') as f:
    kust = yaml.safe_load(f)
kust['namespace'] = '$NAMESPACE'
with open('$PROFILE_DIR/kustomization.yaml', 'w') as f:
    yaml.dump(kust, f, default_flow_style=False, sort_keys=False)
"

# Create namespace + apply all manifests.
kubectl create namespace "$NAMESPACE" 2>/dev/null || true
echo "  Applying manifests..."
kubectl kustomize "$PROFILE_DIR" --load-restrictor LoadRestrictionsNone | kubectl apply -f - 2>&1 | tail -5

# Create ConfigMaps.
echo "  Creating ConfigMaps..."
kubectl -n "$NAMESPACE" create configmap media-stack-bootstrap-config \
  --from-file=config.json="$SCRIPT_DIR/bootstrap/media-stack.bootstrap.json" \
  --dry-run=client -o yaml | kubectl apply -f - 2>&1 | tail -1
kubectl -n "$NAMESPACE" create configmap media-stack-bootstrap-profile \
  --from-file=profile.yaml="$PROFILE_PATH" \
  --dry-run=client -o yaml | kubectl apply -f - 2>&1 | tail -1

# Poll for pods ready.
echo "  Waiting for pods..."
for i in $(seq 1 20); do
    READY=$(kubectl -n "$NAMESPACE" get pods --no-headers 2>&1 | grep -c "1/1" || echo 0)
    TOTAL=$(kubectl -n "$NAMESPACE" get pods --no-headers 2>&1 | wc -l)
    [[ "$READY" -ge 15 ]] && echo "  Pods: $READY/$TOTAL ready" && break
    [[ "$i" -eq 20 ]] && echo "  WARN: Only $READY/$TOTAL pods ready after timeout"
    sleep 10
done

# Poll bootstrap status.
echo "  Waiting for bootstrap..."
for i in $(seq 1 30); do
    POD=$(kubectl -n "$NAMESPACE" get pods -l app=media-stack-bootstrap -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [[ -n "$POD" ]]; then
        PHASE=$(kubectl -n "$NAMESPACE" exec "$POD" -- wget -qO- http://127.0.0.1:9100/status 2>/dev/null | \
            python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null || echo "")
        [[ "$PHASE" == "complete" ]] && echo "  Bootstrap: complete" && break
        [[ "$PHASE" == "error" ]] && echo "  Bootstrap: error (check logs)" && break
    fi
    sleep 15
done

echo ""
echo "Deploy complete: $NAMESPACE"
echo "  Dashboard: http://apps.${NAMESPACE}.local:30180/app/bootstrap-runner/"
echo "  Homepage:  http://apps.${NAMESPACE}.local:30180/app/homepage"
