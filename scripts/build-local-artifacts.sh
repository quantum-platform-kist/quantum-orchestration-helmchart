#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART_ROOT="${ROOT}/quantum-orchestration-helmchart"
TAG="${QC_IMAGE_TAG:-qc-local-$(date -u +%Y%m%d%H%M%S)}"
ARTIFACT_DIR="${ROOT}/.codex-runs/helm-prod-artifacts/${TAG}"
DOCKER="${DOCKER:-docker}"

mkdir -p "${ARTIFACT_DIR}"

run_gradle() {
  local dir="$1"
  local task="$2"
  (cd "${ROOT}/${dir}" && ./gradlew "${task}" -x test --no-daemon)
}

run_yarn_build() {
  local dir="$1"
  (
    cd "${ROOT}/${dir}"
    if [[ ! -d node_modules ]]; then
      corepack yarn install --immutable
    else
      echo "using existing ${dir}/node_modules"
    fi
    corepack yarn build
  )
}

echo "building backend jars with tag ${TAG}"
run_gradle setup-registry :api:bootJar
run_gradle job-orchestrator :api:bootJar
run_gradle setup-a-be :api:bootJar
run_gradle auth-computer :api:bootJar
run_gradle gateway bootJar

echo "building frontend artifacts"
run_yarn_build sdk
run_yarn_build setup-a
run_yarn_build shell

echo "packaging setup-a module artifact"
(cd "${ROOT}/setup-a/dist" && zip -qr "${ARTIFACT_DIR}/setup-a-${TAG}.zip" .)
sha256sum "${ARTIFACT_DIR}/setup-a-${TAG}.zip" > "${ARTIFACT_DIR}/setup-a-${TAG}.zip.sha256"

build_image() {
  local name="$1"
  local dockerfile="$2"
  shift 2
  "${DOCKER}" build -q -t "quantum-cloud-local/${name}:${TAG}" -f "${ROOT}/${dockerfile}" "$@" "${ROOT}" > "${ARTIFACT_DIR}/${name}.image-id"
  "${DOCKER}" image inspect "quantum-cloud-local/${name}:${TAG}" \
    --format '{{.Id}} {{.RepoTags}}' >> "${ARTIFACT_DIR}/images.txt"
}

echo "building local container images"
: > "${ARTIFACT_DIR}/images.txt"
build_image auth deploy/dockerfiles/auth.Dockerfile --build-arg JAR_FILE=auth-computer/api/build/libs/api-NoVersion.jar
build_image gateway deploy/dockerfiles/gateway.Dockerfile --build-arg JAR_FILE=gateway/build/libs/gateway-NoVersion.jar
build_image setup-registry deploy/dockerfiles/setup-registry.Dockerfile --build-arg JAR_FILE=setup-registry/api/build/libs/api-NoVersion.jar
build_image job-orchestrator deploy/dockerfiles/job-orchestrator.Dockerfile --build-arg JAR_FILE=job-orchestrator/api/build/libs/job-orchestrator-api-NoVersion.jar
build_image setup-a-be deploy/dockerfiles/setup-a-be.Dockerfile --build-arg JAR_FILE=setup-a-be/api/build/libs/api-NoVersion.jar
build_image shell deploy/dockerfiles/shell-static.Dockerfile
build_image setup-modules deploy/dockerfiles/setup-modules-static.Dockerfile

cat > "${ARTIFACT_DIR}/images.local.yaml" <<YAML
images:
  auth:
    tag: ${TAG}
  gateway:
    tag: ${TAG}
  setupRegistry:
    tag: ${TAG}
  jobOrchestrator:
    tag: ${TAG}
  setupABe:
    tag: ${TAG}
  shell:
    tag: ${TAG}
  setupModules:
    tag: ${TAG}
YAML

cat > "${ARTIFACT_DIR}/containerd-import-commands.txt" <<EOF
# Run on each target node after copying the tar files.
ctr -n k8s.io images import quantum-cloud-local-${TAG}.tar
EOF

"${DOCKER}" save \
  "quantum-cloud-local/auth:${TAG}" \
  "quantum-cloud-local/gateway:${TAG}" \
  "quantum-cloud-local/setup-registry:${TAG}" \
  "quantum-cloud-local/job-orchestrator:${TAG}" \
  "quantum-cloud-local/setup-a-be:${TAG}" \
  "quantum-cloud-local/shell:${TAG}" \
  "quantum-cloud-local/setup-modules:${TAG}" \
  -o "${ARTIFACT_DIR}/quantum-cloud-local-${TAG}.tar"

echo "artifacts written to ${ARTIFACT_DIR}"
