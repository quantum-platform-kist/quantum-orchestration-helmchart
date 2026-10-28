# Quantum Cloud 운영 Helm 배포

이 저장소는 Quantum Cloud 제품 배포 전용 chart입니다. 공유 `gateway-system/main-gateway`, GatewayClass, cert-manager, MySQL operator, OpenEBS StorageClass는 여기서 만들거나 수정하지 않습니다.

**현재 dev는 기존 온프레미스이며, prod는 별도 NKS 클러스터입니다.** 두 클러스터 모두 namespace 이름은 `quantum-cloud-prod`지만 서로 다른 배포 대상입니다. 개발 CI/CD의 적용 순서와 저장소 설정은 [온프레미스 dev CI/CD](docs/dev-cicd.md)를 따르십시오. 아래 NKS 설치 절차의 신규 개발 namespace·Secret·버킷 예시는 현재 온프레미스 dev에 적용하지 않습니다.

## 기존 개발 배포의 DNS 반영 (2026-10-24)

개발 shell은 `qc-dev.quantum-instrument.com`, modules는 `modules.qc-dev.quantum-instrument.com`, issuer는 `https://qc-dev.quantum-instrument.com`입니다. 두 A 레코드의 올바른 주소는 **`161.122.93.243`**입니다. 2026-10-24 조사 당시 두 레코드의 `175.45.220.187`은 NKS 진입점으로 잘못 설정된 상태였습니다. 온프레미스의 HAProxy TCP 경로는 `10.42.2.2:443` Cilium Gateway로 연결됩니다.

실측 대상은 `kubernetes-admin@prod-k8s`의 `qc-prod-app`과 `qc-prod-stateful`이며 둘 다 Helm `dev`를 추적합니다. 기존 namespace `quantum-cloud-prod`, 앱 Secret `qc-prod-app-secrets`, DB root Secret `qc-mysql-root`, 노드 `prod-k8s-02/03`, `fab-lvm-retain` PVC와 내부 MinIO `setup-modules` 버킷을 유지합니다. `argocd/qc-prod-app.yaml`은 기존 app values 뒤에 DNS override `values/dev-dns.yaml`과 `values/onprem-dev-images/` 8개를 추가합니다. 같은 Application의 `valuesObject.global.gateway`에 `shellSectionName: qc-dev-shell-https`, `moduleSectionName: qc-dev-modules-https`를 지정하여 dev 전용 listener에 연결합니다. 기존 HTTPRoute 이름·backend·namespace와 Gateway 부모 참조는 유지합니다. stateful 데이터 구조는 변경하지 않습니다.

2026-10-24 root는 공유 `gateway-system/main-gateway`의 기존 listener 7개와 `qc-tls`를 보존하고 `qc-dev-shell-https`/`qc-dev-modules-https`를 추가했습니다. 실제 소유 경로는 별도 `argocd/gateway` Application의 `spec.source.kustomize.patches[1].patch` 내 `/spec/listeners` 전체 replace입니다. Gateway 직접 patch나 공유 repo 파일만 수정하면 Application selfHeal로 되돌아갑니다. 별도 `qc-dev-tls` Certificate의 DNS01 발급(`Ready=True`)과 HAProxy 백업·검증·reload도 root가 완료했습니다. `161.122.93.243`으로 resolve한 dev TLS 검증은 성공했으며 아직 dev Route가 없어 HTTP 404였습니다. [운영 예시](infra/public-edge/examples/REVIEW-NOTES.txt)는 이 증거를 설명하는 자료이며 재적용하지 않습니다. 이미 등록된 모듈 manifest의 origin도 확인해야 합니다. publisher는 dev 이미지·셋업·기존 Secret 확인 전까지 비활성입니다. NKS prod의 `argocd/qc-nks-prod-app.yaml`, pause parameters, `values/nks-prod-images/`와 `infra/nks-public-edge`는 그대로 유지합니다.

이번 executor 변경은 로컬 구현과 정적 검증입니다. 위 라이브 작업은 root의 별도 적용 결과입니다. 남은 dev DNS 정정/전파 확인, 원격 브랜치 반영·CI 활성화, `qc-prod-app` sync를 통한 dev Route 연결과 앱 검증은 root가 진행합니다. 보드 실행 워커 기동도 별도 담당입니다.

## NKS 공공 클라우드 신규 설치 참고

이 프로필은 **새 NKS 클러스터/배포에 데이터를 복원하는 경우**에 사용합니다. 이 절의 NKS 개발 프로필은 기존 참고용으로 보존하며 현재 온프레미스 dev CI/CD가 선택하는 프로필이 아닙니다. 기존 PVC의 StorageClass를 제자리에서 변경하지 않습니다. StorageClass·PV·Node·Gateway 자체는 chart가 생성하지 않으며 `volumeName`이나 PV selector로 기존 볼륨을 지정하지 않습니다.

### 사전 준비와 저장·배치 설정

- MSP가 `nks-block-storage-retain` StorageClass와 CSI를 준비하고 실제 reclaim policy가 `Retain`인지 확인해야 합니다. 모든 PVC는 `ReadWriteOnce`이며 최소 10Gi, 10Gi 단위입니다. persistent MySQL에는 현재 chart의 MySQL 버전과 호환되는 MySQL Operator/CRD가 필요하며, 기존 NetworkPolicy가 Operator 통신 원천을 `mysql-operator` namespace로 허용하므로 Operator는 해당 namespace에 준비해야 합니다.
- MSP가 data 노드풀에 `qc.quantum-cloud/role=data` label과 `qc.quantum-cloud/dedicated=data:NoSchedule` taint를 적용합니다. app 노드풀에는 `qc.quantum-cloud/role=app` label을 적용하며 app 전용 taint는 사용하지 않습니다. data Pod는 해당 toleration을 쓰고, 앱·웹·MySQL Router·bootstrap Job·backup Job은 app 노드풀을 사용합니다. override는 기존 hostname selector와 affinity를 명시적 `null`로 제거합니다.
- 기존 Gateway 구현/Gateway API CRD와 대상 Gateway·HTTPS listener를 외부에서 먼저 준비합니다. listener가 QC namespace의 HTTPRoute를 허용해야 하며 TLS 인증서와 서로 다른 shell/modules DNS host도 필요합니다. 차트는 Gateway나 인증서, DNS 레코드를 만들지 않습니다.
- NKS 기본값은 MinIO와 backup 모두 비활성입니다. 각 데이터 볼륨과 외부 S3 API 구조이므로 현재 구성에 NAS는 필요하지 않습니다. backup을 별도로 켜면 20Gi/RWO PVC에 같은 Retain StorageClass를 사용합니다.

| 프로필 | Namespace | MySQL | Kafka | Redis | MinIO를 별도 재활성화할 때 |
|---|---|---|---|---|---|
| NKS 운영 | `quantum-cloud-prod` | 80Gi | 40Gi | 10Gi | 60Gi |
| NKS 개발 | `quantum-cloud-dev` | 20Gi | 10Gi | 10Gi | 10Gi |

MinIO 재활성화 시 내부 저장소를 쓰려면 앱의 `apps.setupRegistry.moduleStorageEndpoint`도 빈 문자열로 바꾸고 bucket/credentials를 함께 검토해야 합니다. endpoint가 비면 기존 `http://qc-minio:9000`으로 돌아갑니다.

### 현장 site-values 필수 입력

NKS 운영·개발 app override는 미확정인 `global.dnsResolver`, `global.trustedProxies`, `global.gateway` 참조를 빈값으로 두고 `httpRoutes.enabled: false`로 시작합니다. `values/nks-prod-app.yaml`은 host/issuer도 비우지만, `values/nks-dev-app.yaml`은 위에서 확정한 두 개발 host와 issuer를 갖습니다. **DNS/host 필수 항목이 비어 있으면 schema 검사에서 실패합니다.** 아래 파일의 `<...>`를 MSP가 확인한 실제 값으로 모두 교체해 별도로 보관하십시오. DNS는 Pod에서 사용할 실제 cluster DNS IP, proxy CIDR은 Auth가 신뢰할 실제 프록시 대역이며 기존 `10.96.0.10`/`10.244.2.0/24`를 그대로 복사하지 않습니다. `issuer`는 shell의 실제 HTTPS origin입니다. 외부 Gateway/listener·TLS·DNS 준비가 끝난 경우에만 아래처럼 HTTPRoute를 켭니다.

운영 `site-values-prod.yaml`:

```yaml
global:
  namespace: quantum-cloud-prod
  dnsResolver: "<실제_CLUSTER_DNS_IP>"
  trustedProxies: "<실제_PROXY_CIDR>"
  hosts:
    shell: "<운영_SHELL_DNS_HOST>"
    modules: "<운영_MODULES_DNS_HOST>"
  issuer: "https://<운영_SHELL_DNS_HOST>"
  gateway:
    name: "<기존_GATEWAY_NAME>"
    namespace: "<기존_GATEWAY_NAMESPACE>"
    shellSectionName: "<운영_SHELL_HTTPS_LISTENER>"
    moduleSectionName: "<운영_MODULES_HTTPS_LISTENER>"
httpRoutes:
  enabled: true
```

새 NKS 개발 설치용 `site-values-dev.yaml`:

```yaml
global:
  namespace: quantum-cloud-dev
  dnsResolver: "<실제_CLUSTER_DNS_IP>"
  trustedProxies: "<실제_PROXY_CIDR>"
  hosts:
    shell: "qc-dev.quantum-instrument.com"
    modules: "modules.qc-dev.quantum-instrument.com"
  issuer: "https://qc-dev.quantum-instrument.com"
  gateway:
    name: "<기존_GATEWAY_NAME>"
    namespace: "<기존_GATEWAY_NAMESPACE>"
    shellSectionName: "<개발_SHELL_HTTPS_LISTENER>"
    moduleSectionName: "<개발_MODULES_HTTPS_LISTENER>"
httpRoutes:
  enabled: true
```

위 placeholder는 배포 가능한 값이 아닙니다. 로컬 렌더 검사의 `192.0.2.53` 같은 DNS IP와 `198.51.100.0/24` 같은 CIDR도 예제용이며 실제 NKS 설정을 의미하지 않습니다. 라우트를 끈 채 검증할 때도 nginx/JWT 설정에 사용할 실제 DNS·host·issuer는 필요합니다. shell/modules origin 분리, iframe 격리, TLS, JWT/JWKS와 기존 계정 동작은 유지합니다.

### Namespace 소유권과 Secret

**Namespace의 유일한 Helm owner는 stateful release입니다.** 새 NKS context를 확인한 뒤, Secret을 먼저 준비하려면 아래처럼 새 namespace를 만들고 stateful release의 Helm 소유 metadata를 붙입니다. `QC_NKS_ENV`는 이번에 설치할 환경 하나를 선택합니다. namespace 생성이 `AlreadyExists`로 실패하면 중단하고 소유권을 확인하십시오. 기존 namespace를 임의로 가져오지 않습니다.

```bash
cd /home/ccxz84/quantum-cloud/quantum-orchestration-helmchart
QC_NKS_ENV=prod # 개발 설치 시 dev
QC_NKS_NAMESPACE="quantum-cloud-${QC_NKS_ENV}"
QC_NKS_STATEFUL_RELEASE="qc-nks-${QC_NKS_ENV}-stateful"

kubectl create namespace "$QC_NKS_NAMESPACE"
kubectl label namespace "$QC_NKS_NAMESPACE" app.kubernetes.io/managed-by=Helm
kubectl annotate namespace "$QC_NKS_NAMESPACE" \
  meta.helm.sh/release-name="$QC_NKS_STATEFUL_RELEASE" \
  meta.helm.sh/release-namespace="$QC_NKS_NAMESPACE"
```

그 namespace에 다음 Secret을 안전한 별도 절차로 생성한 뒤 설치합니다. Secret 값은 저장소·shell history·콘솔·로그에 남기지 않습니다. key 계약은 아래 [필수 Secret 계약](#필수-secret-계약)과 같으며 개발은 이름만 구분합니다. `MODULE_STORAGE_ACCESS_KEY`/`MODULE_STORAGE_SECRET_KEY`에는 해당 환경의 Object Storage 권한을 가진 자격증명을 넣습니다. 서비스 신원과 JWT/비밀번호 계약도 그대로 유지합니다.

| 환경 | MySQL root Secret | 앱/DB/Object Storage Secret | 이미지 pull Secret |
|---|---|---|---|
| 운영 | `qc-mysql-root` | `qc-prod-app-secrets` | `ghcr` |
| 개발 | `qc-dev-mysql-root` | `qc-dev-app-secrets` | `ghcr` |

`--namespace`, stateful/app의 `global.namespace`와 site-values의 namespace는 모두 일치해야 합니다. 앱 chart는 Namespace를 소유하지 않습니다. 이미 준비한 namespace에 설치하므로 아래 명령에는 `--create-namespace`를 붙이지 않습니다.

### 정확한 values 순서와 설치

아래는 현장 값을 채우고 Secret·버킷을 준비한 뒤 실행할 명령입니다. `prod-stateful.yaml`과 `prod-app-images.yaml`을 포함해 기존 고정 이미지를 사용합니다. 표의 왼쪽부터 오른쪽 순서를 지키며 site-values는 환경 프로필 뒤에 둡니다. 개발 DNS-only override를 함께 쓰면 `values/dev-dns.yaml`을 site-values보다 뒤인 마지막에 추가합니다. site-values에는 위 환경 설정만 넣고 replica나 이미지는 변경하지 않습니다.

| 대상 | values 순서 | 앱 replica |
|---|---|---|
| 운영 stateful | `prod-stateful` → `nks-prod-stateful` | — |
| 개발 stateful | `prod-stateful` → `dev-stateful` → `nks-dev-stateful` | — |
| 운영 app staging | `prod-app` → `prod-app-images` → `nks-prod-app` → `site-values-prod` | 0 |
| 운영 app online | `prod-app` → `prod-app-images` → `nks-prod-app` → `prod-app-online` → `site-values-prod` | 1 |
| 개발 app | `prod-app` → `dev-app` → `prod-app-images` → `nks-dev-app` → `site-values-dev` | 1 |

운영은 데이터 복원과 정합성 확인 동안 앱을 0 replica로 유지합니다.

```bash
helm install qc-nks-prod-stateful ./charts/qc-prod-stateful \
  --namespace quantum-cloud-prod \
  -f values/prod-stateful.yaml -f values/nks-prod-stateful.yaml

helm install qc-nks-prod-app ./charts/qc-prod-app \
  --namespace quantum-cloud-prod \
  -f values/prod-app.yaml -f values/prod-app-images.yaml \
  -f values/nks-prod-app.yaml -f site-values-prod.yaml

# DB 복원, 데이터 서비스/bootstrap 완료, 버킷·모듈 파일 정합성을 확인한 뒤 실행합니다.
helm upgrade qc-nks-prod-app ./charts/qc-prod-app \
  --namespace quantum-cloud-prod \
  -f values/prod-app.yaml -f values/prod-app-images.yaml \
  -f values/nks-prod-app.yaml -f values/prod-app-online.yaml \
  -f site-values-prod.yaml
```

개발은 별도 namespace/Secret/버킷을 준비하고 데이터 서비스와 필요한 복원/bootstrap을 마친 다음 앱을 설치합니다.

```bash
helm install qc-nks-dev-stateful ./charts/qc-prod-stateful \
  --namespace quantum-cloud-dev \
  -f values/prod-stateful.yaml -f values/dev-stateful.yaml \
  -f values/nks-dev-stateful.yaml

helm install qc-nks-dev-app ./charts/qc-prod-app \
  --namespace quantum-cloud-dev \
  -f values/prod-app.yaml -f values/dev-app.yaml \
  -f values/prod-app-images.yaml -f values/nks-dev-app.yaml \
  -f site-values-dev.yaml
```

online 운영과 개발의 `job-orchestrator`/`setup-a-be`는 각각 1 replica와 `Recreate`를 유지합니다. 실제 설치 전에 같은 values 순서로 `helm lint`/`helm template`을 실행하고, 설치 후에는 데이터 서비스·bootstrap 및 앱 상태와 실제 동작을 별도로 확인하십시오.

### Object Storage·메일·실연동 확인

NKS 프로필의 Object Storage endpoint는 MSP가 제공한 `https://kr.object.gov-ncloudstorage.com`입니다. 운영은 기존 버킷 `kist-obj`, 개발은 **별도 요청·생성이 필요한** `kist-obj-dev`를 사용합니다. 차트는 외부 버킷을 만들지 않습니다. 앱은 기존 Secret key를 그대로 읽고, `apps.setupRegistry.moduleStorageEndpoint`에 지정한 HTTP/HTTPS endpoint를 사용합니다. 해당 필드의 기본 빈 문자열은 기존 내부 MinIO endpoint를 선택합니다.

endpoint는 HTTP(S) 호스트와 선택적 포트·루트 `/`만 허용합니다(예: `https://object.example.invalid:9443/`). 경로 prefix, query, fragment, userinfo는 허용하지 않습니다.

버킷별 필요한 조회·업로드·다운로드·삭제 권한과 SDK region 자동 탐색에 필요한 bucket location 조회 권한을 확인하고, 기존 모듈 object를 DB 메타데이터의 key와 맞춰 이전해야 합니다. 외부 endpoint 설정만으로 기존 파일이 옮겨지지 않습니다. MSP region은 `gov-standard`이지만 **현재 앱에는 region 설정 기능이 없으며 `MODULE_STORAGE_REGION`을 추가하지 않습니다.** 현재 MinIO SDK의 region 자동 탐색과 NCP 공공 Object Storage의 실제 호환성은 별도 실연동 검증 대상입니다. 자격증명을 준비한 현장에서 실제 공공 endpoint로 bucket location 접근·업로드·다운로드와 Registry의 모듈 upload/activate·조회까지 확인해야 합니다.

메일은 현재 `mock`을 유지합니다. Auth가 지원하는 모드는 `mock`/`smtp`뿐이며 NCP 메일 REST API는 미구현입니다. 정부 endpoint `https://mail.apigw.gov-ntruss.com/api/v1`을 사용하려면 별도의 앱 REST API 구현과 실연동 검증이 필요합니다. 이번 Helm 변경은 새 메일 모드나 앱 이미지를 가정하지 않습니다.

`./scripts/render-check.sh`는 기존 프로필과 NKS 4개 프로필·운영 online의 정적 렌더를 검사합니다. NKS 클러스터 접속과 자격증명 없이 수행하는 렌더 검사는 스토리지 프로비저닝, Object Storage 호환성, TLS·로그인·모듈 및 실제 실험 실행 검증을 대신하지 않습니다. 이번 변경의 NKS 런타임 검증은 미실행입니다.

## 구조

- `charts/qc-prod-stateful`: `quantum-cloud-prod` Namespace의 유일한 owner입니다. QC 전용 MySQL `InnoDBCluster`, Redis, Kafka 3.8 KRaft, MinIO, 초기 topic/bucket/schema bootstrap Job만 렌더링합니다.
- `charts/qc-prod-app`: 앱 Deployment, Service, Nginx ConfigMap, HTTPRoute만 렌더링합니다. Namespace와 Gateway 객체를 만들지 않습니다.
- `values/prod-app.yaml`: restore/cutover 전 기본값입니다. 모든 앱 replica는 `0`입니다.
- `values/prod-app-images.yaml`: GHCR 운영 이미지 repository/tag/digest와 `global.imagePullSecrets: [ghcr]`를 담는 override입니다.
- `values/prod-app-online.yaml`: restore 검증 뒤 앱을 `1` replica로 올리는 override입니다. `job-orchestrator`와 `setup-a-be`는 `Recreate`입니다.
- `manifests/shared-gateway-qc-listeners.reviewed.yaml`: 기존 `qc.*` listener/cert 참고 문서입니다. 현재 온프레미스 Gateway의 실제 override 소유 경로와 dev 추가 내역은 [운영 예시](infra/public-edge/examples/REVIEW-NOTES.txt)를 따릅니다. 이 chart는 Gateway를 소유하지 않습니다.

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

모듈 게시 Job의 로그인은 `modulePublish.credentialsSecretName`으로 별도 Secret을 참조할 수 있습니다. 비어 있으면 기존 `secrets.app.name`을 사용합니다. 별도 Secret은 같은 namespace에 `LOCAL_ADMIN_EMAIL`, `LOCAL_ADMIN_PASSWORD` 키를 준비하며, 비밀값은 Git에 넣지 않습니다. 이 설정은 게시 Job의 로그인 참조만 바꾸고 Auth의 초기 관리자 설정이나 실제 계정 비밀번호는 변경하지 않습니다.

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

## 기존 FAB 운영 배포 순서

다음은 기존 `qc.*` 초기 설치 참고 절차입니다. 현재 온프레미스 dev의 데이터 초기화나 TLS 재적용 절차가 아니며, dev는 [활성화 순서](docs/dev-cicd.md#실제-활성화-순서)를 따릅니다.

1. 공유 Gateway owner가 `qc-shell-https`, `qc-modules-https` listener와 `qc-tls`를 관리합니다. 현재 온프레미스에서는 `argocd/gateway` Application의 `spec.source.kustomize.patches[1].patch`가 `/spec/listeners` 전체를 override하므로 공유 repo 파일만 변경해서는 반영되지 않습니다.
2. 운영 Secret을 생성합니다. 값은 화면과 로그에 출력하지 않습니다.
3. stateful chart를 수동 sync합니다.
4. MySQL 8 dump를 MySQL 9.6 staging/QC DB에 restore하고 Flyway validate/migration, fresh row count 비교를 수행합니다.
5. Kafka topic init, MinIO bucket init, MySQL app schema bootstrap Job 완료를 확인합니다.
6. app chart는 `values/prod-app.yaml`로 먼저 배포하여 replica `0` 상태를 유지합니다.
7. restore/count/module object 정합성 확인 후 `values/prod-app-images.yaml`과 `values/prod-app-online.yaml`을 추가해 검증된 GHCR digest 이미지로 앱을 1 replica씩 올립니다.
8. `setup-a` zip을 Registry upload API로 업로드하고 activate합니다. manifest의 `moduleUrl`, `directApiBaseUrl=https://modules.qc.quantum-instrument.com/api/setup-a`, `connectSrc`를 확인합니다. Shell origin API로 전환한 뒤에는 `directApiBaseUrl=https://qc.quantum-instrument.com/module/setup-a`입니다(`manifests/setup-a-module-shell-origin-api.reviewed.sql`).

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
- Shell nginx는 FAB `/module/<키>` 규약에 따라 `/module/setup-a/setups/{setupId}/setup-a/**`와 `/module/setup-a/local-rbac/**`만 prefix를 떼어 `setup-a-be`로 보내고, 나머지 `/module/setup-a/**`는 404입니다. Cookie는 전달하지 않고 `Set-Cookie`는 숨깁니다. manifest `directApiBaseUrl`이 Shell origin이어야 Shell 권한 관리 화면이 `/local-rbac`를 호출합니다. 기존 modules origin `/api/setup-a` 경로는 호환을 위해 유지합니다.
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
