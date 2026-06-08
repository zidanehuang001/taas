#!/usr/bin/env bash
# Deploy TaaS to a local Kubernetes cluster (gateway + web dashboard + Postgres + Redis + embedded NATS).
#
# Local images taas-gateway:TAG and taas-web:TAG must exist on the node(s), or use TAAS_IMAGE_REGISTRY:
#   1) TAAS_IMAGE_REGISTRY set  -> docker tag + docker push both images + Helm IfNotPresent
#   2) Else                     -> docker save | ctr/k3s ctr import + pullPolicy Never
#
# PostgreSQL/Redis use emptyDir in values-local.yaml so PVC / local-path provisioner is not required.
#
# Full single-node E2E (NATS deploy → dynamo-operator mock path → mock inference + Prometheus):
#   FULLSTACK=1 ./scripts/deploy-local-k8s.sh
# Uses values-fullstack-l20.yaml on top of values-local.yaml (gateway production, operator, mock-dynamo).
#
# Dev gateway + real Dynamo on cluster (no simulated "running"; still TAAS_ENV=development):
#   L20_GPU_DEV=1 ./scripts/deploy-local-k8s.sh
# Uses values-local-gpu-dev.yaml: operator creates DynamoGraphDeployment in dynamoNamespace (default `dynamo`).
# After deploy, set gateway.dynamoFrontendUrl or run scripts/bind-taas-dynamo-frontend.sh for /v1 proxying.
#
# Extra arguments are passed through to `helm upgrade --install`, e.g.:
#   L20_GPU_DEV=1 ./scripts/deploy-local-k8s.sh --set dynamoOperator.dynamoNamespace=dynamo-system

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NAMESPACE="${NAMESPACE:-taas-local}"
RELEASE="${RELEASE:-taas-local}"
IMAGE_TAG="${IMAGE_TAG:-local}"
FULLSTACK="${FULLSTACK:-0}"
L20_GPU_DEV="${L20_GPU_DEV:-0}"
EXTRA_HELM_ARGS=("$@")

IMG_GATEWAY="taas-gateway:${IMAGE_TAG}"
IMG_WEB="taas-web:${IMAGE_TAG}"
IMG_MOCK_DYNAMO="taas-mock-dynamo:${IMAGE_TAG}"
IMG_DYNAMO_OPERATOR="taas-dynamo-operator:${IMAGE_TAG}"

HELM_VALUES_FLAGS=(--values deploy/helm/taas/values-local.yaml)
IMPORT_IMAGES=("${IMG_GATEWAY}" "${IMG_WEB}")

if [[ "${FULLSTACK}" == "1" ]]; then
  HELM_VALUES_FLAGS+=(--values deploy/helm/taas/values-fullstack-l20.yaml)
  IMPORT_IMAGES+=("${IMG_MOCK_DYNAMO}" "${IMG_DYNAMO_OPERATOR}")
fi

if [[ "${L20_GPU_DEV}" == "1" ]]; then
  HELM_VALUES_FLAGS+=(--values deploy/helm/taas/values-local-gpu-dev.yaml)
  IMPORT_IMAGES+=("${IMG_DYNAMO_OPERATOR}")
fi

echo "==> Building gateway image: ${IMG_GATEWAY}"
docker build \
  --build-arg SERVICE=gateway \
  --build-arg VERSION="${IMAGE_TAG}" \
  --build-arg COMMIT=local \
  -t "${IMG_GATEWAY}" \
  -f deploy/docker/Dockerfile .

echo "==> Building web dashboard image: ${IMG_WEB}"
docker build \
  -t "${IMG_WEB}" \
  -f deploy/docker/Dockerfile.web .

if [[ "${FULLSTACK}" == "1" ]]; then
  echo "==> Building mock Dynamo image: ${IMG_MOCK_DYNAMO}"
  docker build \
    --build-arg SERVICE=mock-dynamo \
    --build-arg VERSION="${IMAGE_TAG}" \
    --build-arg COMMIT=local \
    -t "${IMG_MOCK_DYNAMO}" \
    -f deploy/docker/Dockerfile .

  echo "==> Building dynamo-operator image: ${IMG_DYNAMO_OPERATOR}"
  docker build \
    -t "${IMG_DYNAMO_OPERATOR}" \
    -f python/dynamo_operator/Dockerfile \
    python/dynamo_operator
elif [[ "${L20_GPU_DEV}" == "1" ]]; then
  echo "==> Building dynamo-operator image: ${IMG_DYNAMO_OPERATOR}"
  docker build \
    -t "${IMG_DYNAMO_OPERATOR}" \
    -f python/dynamo_operator/Dockerfile \
    python/dynamo_operator
fi

HELM_IMAGE_SET=()

import_images() {
  if docker save "${IMPORT_IMAGES[@]}" | sudo ctr -n k8s.io images import -; then
    return 0
  fi
  if command -v k3s >/dev/null 2>&1 && docker save "${IMPORT_IMAGES[@]}" | sudo k3s ctr -n k8s.io images import -; then
    return 0
  fi
  return 1
}

if [[ -n "${TAAS_IMAGE_REGISTRY:-}" ]]; then
  names=(gateway web)
  [[ "${FULLSTACK}" == "1" ]] && names+=(mock-dynamo dynamo-operator)
  [[ "${L20_GPU_DEV}" == "1" && "${FULLSTACK}" != "1" ]] && names+=(dynamo-operator)
  for name in "${names[@]}"; do
    REMOTE_REF="${TAAS_IMAGE_REGISTRY}/taas-${name}:${IMAGE_TAG}"
    echo "==> Pushing to registry: ${REMOTE_REF}"
    docker tag "taas-${name}:${IMAGE_TAG}" "${REMOTE_REF}"
    docker push "${REMOTE_REF}"
  done
  HELM_IMAGE_SET+=(--set "global.imageRegistry=${TAAS_IMAGE_REGISTRY}")
  HELM_IMAGE_SET+=(--set "image.pullPolicy=IfNotPresent")
else
  echo "==> Importing images into node containerd (k8s.io): ${IMPORT_IMAGES[*]}"
  if import_images; then
    HELM_IMAGE_SET+=(--set "global.imageRegistry=")
    HELM_IMAGE_SET+=(--set "image.pullPolicy=Never")
    LOCAL_IMAGE_NODE_NAME="${LOCAL_IMAGE_NODE_NAME:-$(hostname -s | tr '[:upper:]' '[:lower:]')}"
    HELM_IMAGE_SET+=(--set "localImages.nodeName=${LOCAL_IMAGE_NODE_NAME}")
    echo "==> Pinning locally built images to node: ${LOCAL_IMAGE_NODE_NAME}"
  else
    echo "ERROR: Could not import images into containerd." >&2
    echo "Fix one of:" >&2
    echo "  - Run this script on a host that runs kubelet/containerd and has sudo ctr access, or" >&2
    echo "  - export TAAS_IMAGE_REGISTRY=registry.example.com:5000  (docker login + push), or" >&2
    echo "  - docker save ... | ssh <node> 'sudo ctr -n k8s.io images import -'" >&2
    exit 1
  fi
fi

echo "==> Helm dependencies"
helm dependency update deploy/helm/taas >/dev/null

if [[ "${FRESH_INSTALL:-}" == "1" ]]; then
  echo "==> FRESH_INSTALL=1: removing previous release"
  helm uninstall "${RELEASE}" -n "${NAMESPACE}" 2>/dev/null || true
  kubectl delete pvc --all -n "${NAMESPACE}" 2>/dev/null || true
fi

echo "==> Installing/upgrading ${RELEASE} in namespace ${NAMESPACE}"
helm upgrade --install "${RELEASE}" deploy/helm/taas \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  "${HELM_VALUES_FLAGS[@]}" \
  --set "image.tag=${IMAGE_TAG}" \
  "${HELM_IMAGE_SET[@]}" \
  "${EXTRA_HELM_ARGS[@]}"

PG_POD="$(kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/name=postgresql --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}')"
if [[ -z "${PG_POD}" ]]; then
  echo "ERROR: no PostgreSQL pod found in namespace ${NAMESPACE}" >&2
  exit 1
fi
echo "==> Waiting for PostgreSQL pod: ${PG_POD}"
kubectl wait --for=condition=ready "pod/${PG_POD}" -n "${NAMESPACE}" --timeout=300s
PG_PASS="$(kubectl get secret taas-db-secret -n "${NAMESPACE}" -o jsonpath='{.data.password}' | base64 -d)"

HAS_LITELLM_DB="$(kubectl exec -n "${NAMESPACE}" "${PG_POD}" -- \
  env PGPASSWORD="${PG_PASS}" psql -U taas -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='litellm' LIMIT 1;" 2>/dev/null | tr -d '[:space:]' || true)"
if [[ "${HAS_LITELLM_DB}" != "1" ]]; then
  echo "==> Creating LiteLLM database: litellm"
  kubectl exec -n "${NAMESPACE}" "${PG_POD}" -- \
    env PGPASSWORD="${PG_PASS}" psql -U taas -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE litellm"
fi

HAS_USERS="$(kubectl exec -n "${NAMESPACE}" "${PG_POD}" -- \
  env PGPASSWORD="${PG_PASS}" psql -U taas -d taas -tAc "SELECT to_regclass('public.users');" 2>/dev/null | tr -d '[:space:]' || true)"
if [[ "${HAS_USERS}" == "users" ]]; then
  echo "==> Skipping full migration SQL (public.users already exists)"
else
  echo "==> Applying SQL migrations (fresh DB only — re-run on existing DB would fail on CREATE TABLE)"
  for f in \
    migrations/001_initial_schema.up.sql \
    migrations/002_audit_log.up.sql \
    migrations/003_organizations.up.sql; do
    echo "    $f"
    kubectl exec -i -n "${NAMESPACE}" "${PG_POD}" -- \
      env PGPASSWORD="${PG_PASS}" psql -U taas -d taas -v ON_ERROR_STOP=1 <"$f"
  done
fi

HAS_HF_COL="$(kubectl exec -n "${NAMESPACE}" "${PG_POD}" -- \
  env PGPASSWORD="${PG_PASS}" psql -U taas -d taas -tAc \
  "SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='models' AND column_name='hf_model' LIMIT 1;" 2>/dev/null | tr -d '[:space:]' || true)"
if [[ "${HAS_HF_COL}" != "1" ]]; then
  echo "==> Applying incremental migration: migrations/004_models_hf_model.up.sql"
  kubectl exec -i -n "${NAMESPACE}" "${PG_POD}" -- \
    env PGPASSWORD="${PG_PASS}" psql -U taas -d taas -v ON_ERROR_STOP=1 \
    <"${ROOT}/migrations/004_models_hf_model.up.sql"
fi

if ! kubectl exec -n "${NAMESPACE}" "${PG_POD}" -- \
  env PGPASSWORD="${PG_PASS}" psql -U taas -d taas -tAc "SELECT to_regclass('public.users');" 2>/dev/null | grep -q users; then
  echo "ERROR: table public.users missing. Auth/register will fail until migrations run on a fresh DB (see FRESH_INSTALL=1)." >&2
  exit 1
fi

echo "==> Restart workloads (pullPolicy Never: rollouts pick up newly imported images)"
restart_component() {
  local comp="$1"
  local dep
  dep=$(kubectl get deploy -n "${NAMESPACE}" -l "app.kubernetes.io/component=${comp},app.kubernetes.io/instance=${RELEASE}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -n "${dep}" ]]; then
    kubectl rollout restart "deployment/${dep}" -n "${NAMESPACE}" || true
    kubectl rollout status "deployment/${dep}" -n "${NAMESPACE}" --timeout=180s || true
  fi
}
restart_component gateway
restart_component web
if [[ "${FULLSTACK}" == "1" ]]; then
  restart_component mock-dynamo
  restart_component dynamo-operator
  restart_component prometheus
fi
if [[ "${L20_GPU_DEV}" == "1" && "${FULLSTACK}" != "1" ]]; then
  restart_component dynamo-operator
fi

WEB_SVC=$(kubectl get svc -n "${NAMESPACE}" -l "app.kubernetes.io/component=web,app.kubernetes.io/instance=${RELEASE}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
GW_SVC=$(kubectl get svc -n "${NAMESPACE}" -l "app.kubernetes.io/component=gateway,app.kubernetes.io/instance=${RELEASE}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
LITELLM_SVC=$(kubectl get svc -n "${NAMESPACE}" -l "app.kubernetes.io/component=litellm,app.kubernetes.io/instance=${RELEASE}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

echo ""
echo "Done."
echo "  Dashboard (login / models / tokens):"
if [[ -n "${WEB_SVC}" ]]; then
  WEB_PORT=$(kubectl get svc -n "${NAMESPACE}" "${WEB_SVC}" -o jsonpath='{.spec.ports[0].port}' 2>/dev/null || echo "8080")
  WEB_NODEPORT=$(kubectl get svc -n "${NAMESPACE}" "${WEB_SVC}" -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || true)
  WEB_TYPE=$(kubectl get svc -n "${NAMESPACE}" "${WEB_SVC}" -o jsonpath='{.spec.type}' 2>/dev/null || true)
  echo "    Service: ${WEB_SVC}  (type=${WEB_TYPE})"
  if [[ -n "${WEB_NODEPORT}" ]]; then
    echo "    LAN (NodePort, recommended): http://<node-ip>:${WEB_NODEPORT}  (allow tcp/${WEB_NODEPORT} on the node)"
  fi
  echo "    If web.hostPort > 0: http://<node-ip>:<hostPort> — do not use port-forward on the same port"
  echo "    Port-forward (flaky for remote clients): kubectl port-forward --address 0.0.0.0 -n ${NAMESPACE} svc/${WEB_SVC} 3000:${WEB_PORT}"
else
  echo "    No web Service found (is web.enabled true?). Try: kubectl get svc -n ${NAMESPACE}"
fi
echo "  API only:"
if [[ -n "${GW_SVC}" ]]; then
  echo "    kubectl port-forward -n ${NAMESPACE} svc/${GW_SVC} 8080:8080"
else
  echo "    kubectl get svc -n ${NAMESPACE}  # find *-gateway"
fi
echo "    curl -s http://localhost:8080/health"
if [[ -n "${LITELLM_SVC}" ]]; then
  echo "  LiteLLM data plane:"
  echo "    kubectl port-forward -n ${NAMESPACE} svc/${LITELLM_SVC} 4000:4000"
  echo "    curl -s http://localhost:4000/health"
  echo "    Token/key usage can be inspected from LiteLLM admin endpoints once traffic is generated."
fi
if [[ "${FULLSTACK}" == "1" ]]; then
  echo ""
  echo "  Full-stack extras:"
  echo "    Prometheus: kubectl port-forward -n ${NAMESPACE} svc/${RELEASE}-prometheus 9090:9090"
  echo "    Inference uses in-cluster mock Dynamo (no real L20 workload). For real Dynamo on L20 see values-fullstack-l20.yaml header."
fi
echo ""
echo "If Postgres/Redis were stuck on old PVCs, reinstall with: FRESH_INSTALL=1 $0"
echo "Registry workflow: TAAS_IMAGE_REGISTRY=10.0.0.1:5000 $0"
echo "Full E2E profile: FULLSTACK=1 $0"
echo "Dev env + real Dynamo/L20 (set Frontend URLs after install): L20_GPU_DEV=1 $0"
