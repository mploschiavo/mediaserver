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

# Create namespace + apply all manifests (with namespace override).
kubectl create namespace "$NAMESPACE" 2>/dev/null || true
echo "  Applying manifests..."
kubectl kustomize "$PROFILE_DIR" --load-restrictor LoadRestrictionsNone \
  | sed "s/namespace: media-stack/namespace: $NAMESPACE/g" \
  | kubectl apply -f -

# Create ConfigMaps.
echo "  Creating ConfigMaps..."
kubectl -n "$NAMESPACE" create configmap media-stack-bootstrap-config \
  --from-file=config.json="$SCRIPT_DIR/contracts/media-stack.config.json" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NAMESPACE" create configmap media-stack-bootstrap-profile \
  --from-file=profile.yaml="$PROFILE_PATH" \
  --dry-run=client -o yaml | kubectl apply -f -

# Poll for pods ready.
echo "  Waiting for pods..."
for i in $(seq 1 30); do
    READY=$(kubectl -n "$NAMESPACE" get pods --no-headers 2>/dev/null | grep -c "1/1" || echo "0")
    TOTAL=$(kubectl -n "$NAMESPACE" get pods --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$READY" -ge 10 ]] 2>/dev/null; then
        echo "  Pods: $READY/$TOTAL ready"
        break
    fi
    if [[ "$i" -eq 30 ]]; then
        echo "  WARN: Only $READY/$TOTAL pods ready after timeout"
    fi
    sleep 10
done

# Trigger bootstrap via HTTP API.
echo "  Waiting for bootstrap service..."
POD=""
for i in $(seq 1 40); do
    POD=$(kubectl -n "$NAMESPACE" get pods -l app=media-stack-bootstrap -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [[ -n "$POD" ]]; then
        HEALTH=$(kubectl -n "$NAMESPACE" exec "$POD" -- wget -qO- http://127.0.0.1:9100/healthz 2>/dev/null || echo "")
        [[ -n "$HEALTH" ]] && break
        POD=""
    fi
    sleep 3
done
if [[ -z "$POD" ]]; then
    echo "  ERROR: Bootstrap service pod not found within 120s" >&2
    exit 1
fi

echo "  Triggering bootstrap on pod $POD..."
kubectl -n "$NAMESPACE" exec "$POD" -- \
    wget -qO- --post-data='{}' --header='Content-Type: application/json' \
    http://127.0.0.1:9100/actions/bootstrap 2>/dev/null || true

echo "  Polling bootstrap status..."
for i in $(seq 1 60); do
    PHASE=$(kubectl -n "$NAMESPACE" exec "$POD" -- wget -qO- http://127.0.0.1:9100/status 2>/dev/null | \
        python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null || echo "")
    [[ "$PHASE" == "complete" ]] && echo "  Bootstrap: complete" && break
    [[ "$PHASE" == "error" ]] && echo "  Bootstrap: error (check logs)" && break
    sleep 10
done

echo ""
echo "Deploy complete: $NAMESPACE"
echo "  Dashboard: http://apps.${NAMESPACE}.local:30180/app/media-stack-bootstrap/"
echo "  Homepage:  http://apps.${NAMESPACE}.local:30180/app/homepage"
