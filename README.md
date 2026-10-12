# Quantum Cloud 운영 Helm 배포 준비

이 저장소는 Quantum Cloud 제품 배포 전용 chart입니다. 공유 `gateway-system/main-gateway`, GatewayClass, cert-manager, MySQL operator, OpenEBS StorageClass는 여기서 만들거나 수정하지 않습니다.

## 구조

- `charts/qc-prod-stateful`: `quantum-cloud-prod` Namespace의 유일한 owner입니다. QC 전용 MySQL `InnoDBCluster`, Redis, Kafka 3.8 KRaft, MinIO, 초기 topic/bucket/schema bootstrap Job만 렌더링합니다.
- `charts/qc-prod-app`: 앱 Deployment, Service, Nginx ConfigMap, HTTPRoute만 렌더링합니다. Namespace와 Gateway 객체를 만들지 않습니다.
- `values/prod-app.yaml`: restore/cutover 전 기본값입니다. 모든 앱 replica는 `0`입니다.
- `values/prod-app-images.yaml`: GHCR 운영 이미지 repository/tag/digest와 `global.imagePullSecrets: [ghcr]`를 담는 override입니다.
- `values/prod-app-online.yaml`: restore 검증 뒤 앱을 `1` replica로 올리는 override입니다. `job-orchestrator`와 `setup-a-be`는 `Recreate`입니다.
- `manifests/shared-gateway-qc-listeners.reviewed.yaml`: 공유 infra repo에 반영할 listener/cert 참고 문서입니다. 이 chart의 소유물이 아닙니다.

## 필수 Secret 계약

Secret 값은 git에 두지 않습니다. 기본 chart는 기존 Secret 이름만 참조합니다.

`qc-mysql-root`:

- `rootPassword`

`qc-prod-app-secrets`:

- `AUTH_DB_USERNAME`, `AUTH_DB_PASSWORD`
- `SETUP_REGISTRY_DB_USERNAME`, `SETUP_REGISTRY_DB_PASSWORD`
- `JOB_ORCHESTRATOR_DB_USERNAME`, `JOB_ORCHESTRATOR_DB_PASSWORD`
- `SETUP_A_DB_USERNAME`, `SETUP_A_DB_PASSWORD`
- `MODULE_STORAGE_ACCESS_KEY`, `MODULE_STORAGE_SECRET_KEY`
- `JWT_KEY_ID`, `JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY`, `JWT_REFRESH_SECRET`
- `AUTH_SEED_REGISTRY_CLIENT_SECRET`
- `SETUP_REGISTRY_AUTH_CLIENT_SECRET`
- `JOB_ORCHESTRATOR_AUTH_CLIENT_SECRET`
- `SETUP_A_AUTH_CLIENT_SECRET`
- `LOCAL_ADMIN_EMAIL`, `LOCAL_ADMIN_PASSWORD`, `LOCAL_ADMIN_NICKNAME`

MySQL schema/user bootstrap은 `qc-mysql-app-bootstrap` Job이 Secret을 env로 읽어 수행합니다. Script는 비밀번호를 출력하지 않고, DB 이름과 계정 이름은 allowlist 정규식으로 제한합니다.

## 로컬 검증

```bash
cd /home/ccxz84/quantum-cloud
quantum-orchestration-helmchart/scripts/render-check.sh
```

검증 내용:

- `helm lint`와 `helm template`
- Namespace가 stateful chart에서만 1개 렌더링되는지
- `Gateway` 객체를 렌더링하지 않는지
- data Pod가 `prod-k8s-02`와 `fab.kist/dedicated=data:NoSchedule` toleration을 쓰는지
- app Pod가 `prod-k8s-03`에 고정되는지
- `NodePort`/`LoadBalancer`를 만들지 않는지
- `job-orchestrator`와 `setup-a-be` online override가 `Recreate`인지
- 최종 app 렌더가 `prod-app.yaml`, `prod-app-images.yaml`, `prod-app-online.yaml` 조합으로 7개 Deployment `replicas: 1`, GHCR `sha256` digest 이미지, `ghcr` pull secret, 운영 nginx proxy 설정을 갖는지

## 로컬 artifact/image build

이 chart repo는 `/home/ccxz84/quantum-cloud/quantum-orchestration-helmchart` 하위 repo입니다. 이미지 빌드 스크립트는 상위 workspace의 제품 빌드 산출물과 Docker build 입력을 명시적으로 참조합니다.

- Dockerfile: `/home/ccxz84/quantum-cloud/deploy/dockerfiles/*.Dockerfile`
- 정적 nginx 설정: `/home/ccxz84/quantum-cloud/deploy/nginx/*prod.conf`
- 정적 파일 입력: `/home/ccxz84/quantum-cloud/shell/dist`, `/home/ccxz84/quantum-cloud/sdk/dist`
- backend jar 입력: 각 서비스의 `build/libs/*.jar`

```bash
cd /home/ccxz84/quantum-cloud
QC_IMAGE_TAG=qc-local-20261012-001 quantum-orchestration-helmchart/scripts/build-local-artifacts.sh
```

산출물은 `.codex-runs/helm-prod-artifacts/<tag>/`에 생성됩니다.

- backend jar 기반 이미지 5개
- `shell` static nginx 이미지
- `setup-modules` static nginx 이미지 (`/qc-sdk/` 포함)
- `setup-a-<tag>.zip` module upload artifact
- `images.local.yaml`
- `quantum-cloud-local-<tag>.tar` 오프라인 import용 image archive

노드 import 예시:

```bash
ctr -n k8s.io images import quantum-cloud-local-<tag>.tar
```

로컬 chart 적용 시에는 build script가 만든 `images.local.yaml`을 추가합니다.

```bash
helm template qc-prod-app charts/qc-prod-app \
  -f values/prod-app.yaml \
  -f .codex-runs/helm-prod-artifacts/<tag>/images.local.yaml
```

nginx 설정만 바뀐 경우에는 backend Gradle build나 frontend build를 다시 돌리지 않습니다. 기존 `shell/dist`와 `sdk/dist`를 그대로 사용해 아래 두 static 이미지만 새 immutable tag로 빌드합니다.

```bash
docker build -t quantum-cloud-local/shell:<tag> -f deploy/dockerfiles/shell-static.Dockerfile .
docker build -t quantum-cloud-local/setup-modules:<tag> -f deploy/dockerfiles/setup-modules-static.Dockerfile .
```

운영 배포에는 GHCR에 push된 immutable tag와 RepoDigest를 `values/prod-app-images.yaml`에 기록합니다. 이 파일은 backend 5개 digest JSON과 새 static image의 `docker image inspect` RepoDigests를 프로그램으로 읽어 생성하며, digest를 손으로 옮겨 적지 않습니다. Argo CD Application은 다음 값 파일을 순서대로 사용합니다.

```yaml
valueFiles:
  - ../../values/prod-app.yaml
  - ../../values/prod-app-images.yaml
  - ../../values/prod-app-online.yaml
```

## 배포 순서

1. 공유 infra repo에서 `qc-shell-https`, `qc-modules-https` listener와 `qc-tls` SAN을 반영합니다. 이 작업은 `quantum-cloud-helmchart` owner 경로에서만 수행합니다.
2. 운영 Secret을 생성합니다. 값은 화면과 로그에 출력하지 않습니다.
3. stateful chart를 수동 sync합니다.
4. MySQL 8 dump를 MySQL 9.6 staging/QC DB에 restore하고 Flyway validate/migration, fresh row count 비교를 수행합니다.
5. Kafka topic init, MinIO bucket init, MySQL app schema bootstrap Job 완료를 확인합니다.
6. app chart는 `values/prod-app.yaml`로 먼저 배포하여 replica `0` 상태를 유지합니다.
7. restore/count/module object 정합성 확인 후 `values/prod-app-images.yaml`과 `values/prod-app-online.yaml`을 추가해 검증된 GHCR digest 이미지로 앱을 1 replica씩 올립니다.
8. `setup-a` zip을 Registry upload API로 업로드하고 activate합니다. manifest의 `moduleUrl`, `directApiBaseUrl=https://modules.qc.quantum-instrument.com/api/setup-a`, `connectSrc`를 확인합니다.

Module upload 예시:

```bash
VERSION="0.1.0-prod-$(date -u +%Y%m%d%H%M%S)"
ZIP=".codex-runs/helm-prod-artifacts/<tag>/setup-a-<tag>.zip"
SHA256="$(cut -d' ' -f1 "${ZIP}.sha256")"

curl -fS \
  -H "Authorization: Bearer ${QC_ADMIN_TOKEN}" \
  -F "version=${VERSION}" \
  -F "sha256=${SHA256}" \
  -F 'manifestMetadata={"moduleNav":[{"key":"m_run","label":"Run","default":true}]};type=application/json' \
  -F "file=@${ZIP};type=application/zip" \
  "https://qc.quantum-instrument.com/server/api/setups/stp_nv_a1/module-versions"

curl -fS -X POST \
  -H "Authorization: Bearer ${QC_ADMIN_TOKEN}" \
  "https://qc.quantum-instrument.com/server/api/setups/stp_nv_a1/module-versions/${VERSION}/activate"
```

Restore된 `setup_module_manifests`가 아직 dev URL을 들고 있으면 `manifests/setup-a-module-prod-manifest.reviewed.sql`을 검토 후 수동 적용합니다. 이 SQL은 GitOps 자동 실행 대상이 아닙니다.

RFSoC worker start/cancel consumer, end publisher daemon, 외부 Kafka TLS/SASL/ACL, DNS/TLS/외부 443 경로, 실제 브라우저/하드웨어 E2E는 별도 gate입니다. 이 chart만으로 실험 실행 완료를 주장하지 않습니다.

## 핵심 라우팅

- Shell nginx는 `/server/**`에서 `/server` prefix를 제거해 Spring Gateway `/api/**`로 보냅니다.
- Auth `Set-Cookie Path=/api/auth`와 logout deletion cookie는 `/server/api/auth`로 rewrite합니다.
- Modules nginx는 `/module-assets/**`를 Registry로 보냅니다. MinIO를 브라우저에 직접 노출하지 않습니다.
- Modules nginx는 `/api/setup-a/setups/{setupId}/setup-a/**`만 실제 `setup-a-be`의 `/setups/{setupId}/setup-a/**` REST/WS endpoint로 보냅니다.
- Shell origin과 modules origin은 분리됩니다. iframe setup scoped token MessagePort 격리를 유지합니다.
