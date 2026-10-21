# Quantum Cloud 운영 Helm 배포

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


## 운영 인증 설정

`apps.auth.allowDefaultAdmin`의 chart 기본값은 `false`이며, 현재 `values/prod-app.yaml`은 사용자가 지정한 `admin@local`을 사용하기 위해 `true`로 설정합니다. 이 값이 `LOCAL_ADMIN_ALLOW_DEFAULT`로 전달되어 Auth의 기본 관리자 식별자 사용 제한을 해제합니다. 이메일·비밀번호는 기존 Secret에서 읽으며, `LOCAL_ADMIN_RESET_PASSWORD_ON_START=false`이므로 재시작마다 기존 계정의 비밀번호를 초기화하지 않습니다.

Registry의 서비스 신원은 `platform:setup-registry`입니다. `AUTH_SEED_REGISTRY_CLIENT_SECRET`과 `SETUP_REGISTRY_AUTH_CLIENT_SECRET`에는 동일한 자격증명을 넣습니다. 이 계정은 셋업 서비스 계정을 발급하는 용도입니다.

현재 단일 셋업 배포에서 Job 서버와 Setup A 백엔드는 `setup-be:<global.moduleSetupId>` 신원으로 Auth에 권한 판정을 요청합니다. Auth의 내부 `/oauth2/token`에서 Registry 서비스 토큰을 발급받은 뒤 `/internal/service-accounts`에 `setupId`를 전달해 셋업 계정을 생성합니다. 반환된 `clientSecret`을 `JOB_ORCHESTRATOR_AUTH_CLIENT_SECRET`, `SETUP_A_AUTH_CLIENT_SECRET`에 저장하고 두 백엔드를 재시작합니다. 내부 API는 SSH 터널 또는 클러스터 내부에서만 호출합니다.

계정 생성 API는 같은 `setupId`로 재호출하면 Secret을 회전하므로 기존 계정과 보관된 Secret을 먼저 확인해야 합니다. 현재 배포는 `stp_nv_a1` 한 개를 대상으로 하며, 여러 셋업을 중앙 Job 서버에 연결할 때는 서비스 신원 관리 설계가 추가로 필요합니다.

다섯 Spring 서비스의 `SERVER_FORWARD_HEADERS_STRATEGY=framework`는 nginx가 설정한 HTTPS 전달 헤더를 반영합니다. 브라우저 로그인과 쿠키 처리에 필요한 설정입니다.

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

## PVC 없는 임시 검증 배포

기본 `global.storageMode`는 `persistent`입니다. 새 `values/ephemeral-stateful.yaml`을 선택하면 MySQL·Kafka·Redis·MinIO의 데이터 경로를 디스크 기반 `emptyDir`로 만듭니다. PVC/PV/StorageClass, MySQL Operator/InnoDBCluster 없이 설치할 수 있습니다. 앱의 `httpRoutes.enabled` 기본값은 `true`이며, `ephemeral-app.yaml`은 이를 `false`로 설정하므로 Gateway API CRD도 필요하지 않습니다.

**Pod 삭제·교체·재스케줄 또는 클러스터 폐기 시 데이터가 소실됩니다.** 같은 Pod 안의 컨테이너 재시작은 `emptyDir`를 지우지 않습니다. MySQL의 네 빈 schema/사용자, Kafka의 일곱 토픽, MinIO의 `setup-modules` 버킷은 새 Pod에서 자동 재초기화되지만 영속 데이터·실험 이력·모듈 artifact는 복구되지 않습니다. Redis는 빈 데이터로 시작합니다. `emptyDir.sizeLimit`은 용량 예약이 아니며 노드 디스크 여유가 필요합니다. 임시 Kafka retention 합계는 112MiB, budget은 256MiB이며 실제 디스크 사용량의 엄밀한 상한은 아닙니다.

**반드시 새 namespace와 새 release에 `helm install`하십시오. 기존 release를 persistent ↔ ephemeral로 전환하는 `upgrade`는 금지합니다.** 운영 `quantum-cloud-prod`에는 적용하지 않습니다. 기본 임시 namespace는 `quantum-cloud-ephemeral`이고 다른 새 namespace도 가능합니다. stateful의 `global.namespace`와 `--namespace`는 일치해야 합니다. 앱도 HTTPRoute를 끈 경우 두 값의 일치를 검사합니다. `ephemeral`과 `backup.enabled=true` 조합은 PVC backup 충돌로 렌더 오류가 납니다. Argo/prod/dev values와 `dev` 브랜치는 기존대로 유지합니다.

아래 명령은 **이 저장소 폴더 안**에서, 사용자가 선택한 임시 클러스터/context를 대상으로 실행하는 순서입니다. 설치 전에 대상 context가 임시 환경인지 확인하십시오. stateful chart가 Namespace를 소유하므로 새 namespace를 먼저 생성하고 Helm 소유 metadata를 지정한 뒤 Secret을 준비합니다. 이미 사용 중인 namespace의 소유권을 변경하지 마십시오. namespace 생성이 `AlreadyExists`로 실패하면 이후 명령을 진행하지 말고 다른 새 namespace를 선택하십시오. 첫 설치에 `--create-namespace`, `--wait`, `--atomic`을 붙이지 않습니다.

```bash
cd /home/ccxz84/quantum-cloud/quantum-orchestration-helmchart
QC_EPHEMERAL_NAMESPACE=quantum-cloud-ephemeral

kubectl create namespace "$QC_EPHEMERAL_NAMESPACE"
kubectl label namespace "$QC_EPHEMERAL_NAMESPACE" app.kubernetes.io/managed-by=Helm
kubectl annotate namespace "$QC_EPHEMERAL_NAMESPACE" \
  meta.helm.sh/release-name=qc-ephemeral-stateful \
  meta.helm.sh/release-namespace="$QC_EPHEMERAL_NAMESPACE"
```

Namespace 생성과 Helm 소유 metadata 지정이 끝나면, Helm 설치 전에 그 namespace 안에 다음 Secret을 별도로 준비하십시오. 운영 Secret을 복사하거나 읽지 말고 임시 자격증명을 사용하십시오. Secret 값은 안전한 기존 입력 수단으로 주입하고 shell history·콘솔·로그·저장소에 남기지 않습니다. 차트는 Secret 객체를 생성하지 않습니다.

| Secret | 필요한 key |
|---|---|
| `qc-ephemeral-mysql-root` | `rootPassword` — 새 임시 root 값은 `[A-Za-z0-9_-]` 문자만으로 24자 이상 |
| `qc-ephemeral-app-secrets` — 데이터 서비스 | `AUTH_DB_USERNAME`, `AUTH_DB_PASSWORD`, `SETUP_REGISTRY_DB_USERNAME`, `SETUP_REGISTRY_DB_PASSWORD`, `JOB_ORCHESTRATOR_DB_USERNAME`, `JOB_ORCHESTRATOR_DB_PASSWORD`, `SETUP_A_DB_USERNAME`, `SETUP_A_DB_PASSWORD`, `MODULE_STORAGE_ACCESS_KEY`, `MODULE_STORAGE_SECRET_KEY` |
| 같은 app Secret — 앱용 | `JWT_KEY_ID`, `JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY`, `JWT_REFRESH_SECRET`, `AUTH_SEED_REGISTRY_CLIENT_SECRET`, `SETUP_REGISTRY_AUTH_CLIENT_SECRET`, `JOB_ORCHESTRATOR_AUTH_CLIENT_SECRET`, `SETUP_A_AUTH_CLIENT_SECRET`, `LOCAL_ADMIN_EMAIL`, `LOCAL_ADMIN_PASSWORD`, `LOCAL_ADMIN_NICKNAME` |
| `ghcr` | 해당 namespace에서 기존 고정 GHCR 이미지를 받을 imagePullSecret |

네 DB username은 서로 다르게 준비하며 각 계정은 자기 DB에만 권한을 받습니다. `AUTH_SEED_REGISTRY_CLIENT_SECRET`과 `SETUP_REGISTRY_AUTH_CLIENT_SECRET`은 동일해야 합니다. 앱의 기본 관리자 허용은 `false`이므로 임시 관리자 정보도 이 정책에 맞게 준비합니다. Secret/imagePullSecret 준비 전의 Pending/CreateContainerConfigError/ImagePullBackOff는 설치 완료 상태가 아닙니다.

아래 **values 순서**로 기존 이미지 digest와 `prod-stateful.yaml`의 MinIO/`mc` GHCR mirror를 먼저 상속하고 임시 저장·배치 설정만 덮어씁니다. Docker Hub의 예전 MinIO/`mc` 태그 제공 중단 때문에 실제 설치 시 `prod-stateful.yaml`을 생략하지 마십시오. ephemeral 파일 단독 렌더는 가능하지만 이미지 다운로드 성공을 보장하지 않습니다.

```bash
# 별도 Secret 준비가 끝난 뒤 stateful chart를 설치합니다.
helm install qc-ephemeral-stateful ./charts/qc-prod-stateful \
  --namespace "$QC_EPHEMERAL_NAMESPACE" \
  -f values/prod-stateful.yaml \
  -f values/ephemeral-stateful.yaml \
  --set-string global.namespace="$QC_EPHEMERAL_NAMESPACE"

# 데이터 서비스 기동을 기다립니다.
for workload in qc-mysql qc-kafka qc-redis qc-minio; do
  kubectl --namespace "$QC_EPHEMERAL_NAMESPACE" rollout status \
    "statefulset/$workload" --timeout=900s
done

# 앱은 prod-app.yaml / prod-app-online.yaml 없이 아래 두 파일만 순서대로 사용합니다.
helm install qc-ephemeral-app ./charts/qc-prod-app \
  --namespace "$QC_EPHEMERAL_NAMESPACE" \
  -f values/prod-app-images.yaml \
  -f values/ephemeral-app.yaml \
  --set-string global.namespace="$QC_EPHEMERAL_NAMESPACE" \
  --set-string global.gateway.namespace="$QC_EPHEMERAL_NAMESPACE"
```

앱은 기존 digest의 7개 Deployment를 replica 1로 렌더링합니다. 빈 DB에 대한 앱 Flyway 실행, 서비스 계정 온보딩, 셋업 등록, 모듈 upload/activate는 별도 절차입니다. MySQL Pod 교체 후에도 앱의 전체 재온보딩을 자동으로 수행하지 않습니다.

임시 호스트 `qc-ephemeral.quantum-instrument.com`과 `modules.qc-ephemeral.quantum-instrument.com`, `qc-ephemeral-gateway` 참조는 **환경 설정값**이며 DNS/TLS/Gateway listener를 실제 생성하지 않습니다. `global.dnsResolver`도 대상 클러스터의 DNS resolver 주소로 구성해야 하는 환경 설정값이며 기본 `10.96.0.10`이 모든 환경에 맞는 것은 아닙니다. 외부 접근에는 별도의 DNS·TLS·Gateway 및 `global.trustedProxies` 설정이 필요합니다. 라우트를 켜면 그 임시 host/listener 참조의 HTTPRoute 두 개만 생성합니다. 기존 HTTPS 쿠키, JWT/JWKS, nginx, CORS/CSP, iframe origin 격리는 유지합니다.

MySQL 초기화가 중간에 실패하면 완료 marker가 없어 Ready가 되지 않습니다. 자동 데이터 삭제는 하지 않습니다. 원인과 임시 Secret을 수정한 뒤 **해당 임시 namespace의 MySQL Pod만 재생성**하면 새 `emptyDir`에서 다시 초기화됩니다. 이 복구도 기존 임시 DB 데이터를 모두 잃으므로 다른 namespace/release에 실행하지 마십시오.

렌더 회귀 검사는 `./scripts/render-check.sh`로 실행합니다(Helm 3, Python 3, PyYAML 필요). 기존 9개 조합과 임시 프로필, 네 data `emptyDir`, 금지 스토리지/운영 참조 부재, namespace 변경, route toggle, 잘못된 mode/bool·backup 조합 실패를 검사합니다. 이 정적 검사는 실제 서비스 기동·입출력·Pod 교체 검증을 대신하지 않습니다.

2026-10-21 로컬 kind(Kubernetes 1.31.4) 검증에서 PVC/PV/StorageClass 각각 0개, 데이터 StatefulSet 4개와 앱 Deployment 7개 Ready를 확인했습니다. 네 데이터 서비스 입출력, 각 Pod 교체 후 빈 저장소 재초기화와 기존 데이터 소실, Gateway를 통한 임시 관리자 로그인·사용자 조회도 통과했습니다. 이 검증은 외부 DNS/TLS 설정이나 셋업·모듈 온보딩 완료를 의미하지 않습니다.
