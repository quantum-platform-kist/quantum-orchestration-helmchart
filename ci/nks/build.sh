#!/usr/bin/env bash
set -euo pipefail
ci_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from common import read_snapshot, validate_snapshot; validate_snapshot(read_snapshot())' "$ci_dir"
alias="$(python3 -c 'import json; print(json.load(open("build/source-snapshot.json"))["alias"])')"
runtime="$(python3 -c 'import json; print(json.load(open("build/source-snapshot.json"))["runtime"])')"
build_prefix=nks
if [[ "${NKS_DEPLOYMENT_ENVIRONMENT:-prod}" == dev ]]; then build_prefix=onprem-dev; fi
export NKS_BUILD_VERSION="0.0.0-${build_prefix}-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
export VERSION="$NKS_BUILD_VERSION"
export GRADLE_OPTS="-Dmaven.repo.local=${RUNNER_TEMP:?}/nks-m2"
export GRADLE_USER_HOME="$RUNNER_TEMP/nks-gradle"

gradle() {
  local project="$1"; shift
  (
    cd "$root/workspace/$project"
    chmod +x gradlew
    ./gradlew "$@" --no-daemon --max-workers=2 --init-script "$ci_dir/local-dependencies.init.gradle" \
      "-Dmaven.repo.local=$RUNNER_TEMP/nks-m2" \
      "-Pquantum-cloud-core=$NKS_BUILD_VERSION" \
      "-Pauth-api-client=$NKS_BUILD_VERSION" \
      "-Pauth-core-version=$NKS_BUILD_VERSION" \
      "-PauthCoreVersion=$NKS_BUILD_VERSION" \
      "-Psetup-registry-api-client=$NKS_BUILD_VERSION" \
      "-Pjob-orchestrator-api-client=$NKS_BUILD_VERSION" \
      '-Pquantum-cloud-config=2026.02.14'
  )
}

yarn_build() {
  local project="$1"
  (
    cd "$root/workspace/$project"
    corepack yarn install --immutable
    corepack yarn typecheck
    if node -e 'process.exit(require("./package.json").scripts.test ? 0 : 1)'; then corepack yarn test; fi
    corepack yarn build
  )
}

if [[ "$runtime" == java ]]; then
  [[ -n "${PACKAGE_READ_TOKEN:-}" ]] || { echo 'PACKAGE_READ_TOKEN is required for private Maven dependencies'; exit 1; }
  if [[ -d "$root/workspace/core" ]]; then
    gradle core -p quantum-cloud-cores check publishToMavenLocal
  fi
  if [[ -d "$root/workspace/auth" ]]; then
    gradle auth check :api:jar :api:e2eTest :api:kafkaTest \
      :api-client:publishAuthApiClientPublicationToMavenLocal \
      :api:publishAuthCorePublicationToMavenLocal
  fi
  case "$alias" in
    core|auth) ;;
    auth-computer) gradle auth-computer check :api:bootJar ;;
    gateway) gradle gateway check bootJar ;;
    setup-registry) gradle setup-registry check :api-client:publishToMavenLocal :api:bootJar ;;
    job-orchestrator)
      gradle setup-registry :api-client:check :api-client:publishToMavenLocal
      gradle job-orchestrator check :api-client:publishToMavenLocal :api:bootJar ;;
    setup-a-be)
      gradle setup-registry :api-client:check :api-client:publishToMavenLocal
      gradle job-orchestrator :api-client:check :api-client:publishToMavenLocal
      gradle setup-a-be check :api:bootJar ;;
    *) echo 'Unknown Java target'; exit 1 ;;
  esac
elif [[ "$runtime" == node ]]; then
  corepack enable
  corepack prepare yarn@4.10.3 --activate
  [[ ! -d "$root/workspace/sdk" ]] || yarn_build sdk
  [[ ! -d "$root/workspace/ui" ]] || yarn_build ui
  case "$alias" in
    sdk|ui) ;;
    shell|setup-a) yarn_build "$alias" ;;
    *) echo 'Unknown Node target'; exit 1 ;;
  esac
else
  echo 'Unknown build runtime'; exit 1
fi

python3 "$ci_dir/package_images.py"
