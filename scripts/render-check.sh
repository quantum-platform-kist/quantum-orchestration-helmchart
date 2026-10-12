#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT}/rendered"
mkdir -p "${OUT_DIR}"

helm lint "${ROOT}/charts/qc-prod-stateful" -f "${ROOT}/values/prod-stateful.yaml"
helm lint "${ROOT}/charts/qc-prod-app" -f "${ROOT}/values/prod-app.yaml"
helm lint "${ROOT}/charts/qc-prod-app" -f "${ROOT}/values/prod-app.yaml" -f "${ROOT}/values/prod-app-online.yaml"
helm lint "${ROOT}/charts/qc-prod-app" \
  -f "${ROOT}/values/prod-app.yaml" \
  -f "${ROOT}/values/prod-app-images.yaml" \
  -f "${ROOT}/values/prod-app-online.yaml"

helm template qc-prod-stateful "${ROOT}/charts/qc-prod-stateful" \
  -f "${ROOT}/values/prod-stateful.yaml" \
  > "${OUT_DIR}/qc-prod-stateful.yaml"

helm template qc-prod-app "${ROOT}/charts/qc-prod-app" \
  -f "${ROOT}/values/prod-app.yaml" \
  > "${OUT_DIR}/qc-prod-app.yaml"

helm template qc-prod-app "${ROOT}/charts/qc-prod-app" \
  -f "${ROOT}/values/prod-app.yaml" \
  -f "${ROOT}/values/prod-app-online.yaml" \
  > "${OUT_DIR}/qc-prod-app-online.yaml"

helm template qc-prod-app "${ROOT}/charts/qc-prod-app" \
  -f "${ROOT}/values/prod-app.yaml" \
  -f "${ROOT}/values/prod-app-images.yaml" \
  -f "${ROOT}/values/prod-app-online.yaml" \
  > "${OUT_DIR}/qc-prod-app-final.yaml"

namespace_count="$(grep -c '^kind: Namespace$' "${OUT_DIR}/qc-prod-stateful.yaml" || true)"
if [[ "${namespace_count}" != "1" ]]; then
  echo "expected exactly one Namespace in stateful chart, got ${namespace_count}" >&2
  exit 1
fi

if grep -q '^kind: Namespace$' "${OUT_DIR}/qc-prod-app.yaml"; then
  echo "app chart must not render Namespace" >&2
  exit 1
fi

if grep -q '^kind: Gateway$' "${OUT_DIR}/qc-prod-app.yaml" "${OUT_DIR}/qc-prod-stateful.yaml"; then
  echo "QC charts must not own gateway-system/main-gateway" >&2
  exit 1
fi

grep -q 'kubernetes.io/hostname: prod-k8s-02' "${OUT_DIR}/qc-prod-stateful.yaml"
grep -q 'fab.kist/dedicated' "${OUT_DIR}/qc-prod-stateful.yaml"
grep -q 'kubernetes.io/hostname: prod-k8s-03' "${OUT_DIR}/qc-prod-app.yaml"
grep -q 'sectionName: qc-shell-https' "${OUT_DIR}/qc-prod-app.yaml"
grep -q 'sectionName: qc-modules-https' "${OUT_DIR}/qc-prod-app.yaml"
grep -q 'type: Recreate' "${OUT_DIR}/qc-prod-app-online.yaml"
grep -q 'client_max_body_size 25m;' "${OUT_DIR}/qc-prod-app-final.yaml"
grep -q 'gateway.quantum-cloud-prod.svc.cluster.local:8080' "${OUT_DIR}/qc-prod-app-final.yaml"
grep -q 'setup-registry.quantum-cloud-prod.svc.cluster.local:8085' "${OUT_DIR}/qc-prod-app-final.yaml"
grep -q 'setup-a-be.quantum-cloud-prod.svc.cluster.local:8086' "${OUT_DIR}/qc-prod-app-final.yaml"
grep -F -q 'rewrite ^/server/?(.*)$ /$1 break;' "${OUT_DIR}/qc-prod-app-final.yaml"
grep -q 'name: "ghcr"' "${OUT_DIR}/qc-prod-app-final.yaml"

replica_one_count="$(grep -c '^  replicas: 1$' "${OUT_DIR}/qc-prod-app-final.yaml" || true)"
if [[ "${replica_one_count}" != "7" ]]; then
  echo "expected final app render to contain seven replica: 1 deployments, got ${replica_one_count}" >&2
  exit 1
fi

if grep -q '^  replicas: 0$' "${OUT_DIR}/qc-prod-app-final.yaml"; then
  echo "final app render must not contain replica: 0 deployments" >&2
  exit 1
fi

image_digest_count="$(grep -Ec 'image: ghcr\.io/quantum-platform-kist/quantum-orchestration-helmchart/[^ ]+@sha256:[0-9a-f]{64}$' "${OUT_DIR}/qc-prod-app-final.yaml" || true)"
if [[ "${image_digest_count}" != "7" ]]; then
  echo "expected final app render to contain seven ghcr sha256 image digests, got ${image_digest_count}" >&2
  exit 1
fi

if grep -Eq 'type: (NodePort|LoadBalancer)' "${OUT_DIR}/qc-prod-stateful.yaml" "${OUT_DIR}/qc-prod-app.yaml"; then
  echo "stateful/app services must stay private ClusterIP only" >&2
  exit 1
fi

echo "render checks passed: ${OUT_DIR}"
