# 온프레미스 dev CI/CD

2026-10-24 기준, **dev는 기존 온프레미스, prod는 별도 NKS**입니다. `nks-reusable.yml`, `ci/nks`, `dev-nks.yml`과 `NKS_*` Secret/variable 이름은 기존 호출 계약의 호환성을 위해 유지합니다. 이름에 NKS가 있어도 `deployment_environment: dev`는 아래 온프레미스 계약을 선택합니다.

## 배포 대상과 보존할 자원

| 항목 | dev: 기존 온프레미스 | prod: 기존 NKS |
|---|---|---|
| source / tooling / GitOps branch | `dev` | `prod` |
| caller / enable variable | `dev-nks.yml` / `NKS_DEV_CI_ENABLED` | `prod-nks.yml` / `NKS_PROD_CI_ENABLED` |
| 이미지 values | `values/onprem-dev-images/` | `values/nks-prod-images/` |
| Argo app / stateful | `qc-prod-app` / `qc-prod-stateful` | `qc-nks-prod-app` / `qc-nks-prod-stateful` |
| namespace | `quantum-cloud-prod` | `quantum-cloud-prod` (다른 클러스터) |
| 앱 / DB root Secret | `qc-prod-app-secrets` / `qc-mysql-root` | 기존 값 유지 |
| shell origin | `https://qc-dev.quantum-instrument.com` | `https://qc.quantum-instrument.com` |
| modules origin | `https://modules.qc-dev.quantum-instrument.com` | `https://modules.qc.quantum-instrument.com` |
| 모듈 저장소 | 내부 `qc-minio`, bucket `setup-modules` | 기존 NKS Object Storage |
| 공개 진입점 | `161.122.93.243` → HAProxy TCP → `10.42.2.2:443` | 기존 NKS ALB |

온프레미스 실측 context는 `kubernetes-admin@prod-k8s`입니다. `qc-prod-app`과 `qc-prod-stateful`은 `dev`를 추적하며, 2026-10-24 23:28 KST 독립 확인에서 `e77d54d` 기준 Synced/Healthy였습니다. 기존 7개 앱 Deployment의 desired/ready/available/updated가 모두 1이고, 실제 Pod `imageID` 7개가 dev CI 산출물의 SHA256 digest와 일치했습니다(restartCount=0). 기존 Job 162건(done 76/canceled 84/failed 2)도 보존됐습니다. 이 구성은 새 클러스터 설치가 아닙니다. 기존 DB/Job/측정 데이터, Secret 값, PVC, 노드 `prod-k8s-02/03`, StorageClass `fab-lvm-retain`, DNS resolver `10.96.0.10`, trusted proxy `10.244.2.0/24`를 유지합니다. 새 namespace·NAS·S3 bucket을 준비할 필요가 없습니다.

`qc-prod-app.yaml`의 values 순서는 기존 `prod-app` → `prod-app-images` → `prod-app-online` → `dev-dns` → `onprem-dev-images` 8종입니다. `valuesObject.global.gateway`의 `shellSectionName: qc-dev-shell-https`, `moduleSectionName: qc-dev-modules-https`로 HTTPRoute가 dev listener에 연결됐습니다. 기존 Route 이름·backend·namespace와 `gateway-system/main-gateway` 부모는 유지합니다. `valuesObject.global.deploymentEnvironment=dev`이며 앱 자동 동기화는 `selfHeal=true`, `prune=false`입니다. 이번 변경은 온프레미스 Application의 `modulePublish.enabled=false` 상위 parameter를 제거하여 환경별 values가 게시 여부를 결정하도록 합니다. dev values에는 환경 프로비넌스 확인이 끝난 신규 publisher image/version이 들어 있습니다. 실제 Application 적용, PostSync upload/activate와 E2E 검증은 백엔드 bridge 배포 준비 후 root가 수행합니다. `qc-prod-stateful.yaml`의 현재 구조와 수동 동기화는 유지합니다. NKS prod app의 Registry 정지/publisher 정지 parameters, 이미지 digest와 edge 소유권도 보존합니다.

## 11개 앱 저장소의 브랜치와 caller

각 저장소의 clean clone은 `.codex-runs/dev-cicd-20261024/repos/<alias>`에 있으며 `origin/prod`에서 시작한 `codex/dev-cicd`입니다. root가 11개 저장소의 caller와 필수 prod 수정을 `dev`에 반영했고, Helm `dev` 반영도 완료했습니다. 원본 개발 작업트리는 보존합니다. 이번 Job bridge 수정은 architect 계획에 따라 원본의 필요한 delegated API 부분만 job-orchestrator clean clone에 이식하며 별도 dev 반영 대상입니다.

| alias | `quantum-platform-kist/` 저장소 | caller packages 권한 |
|---|---|---|
| core | `quantum-platform-core` | `read` |
| auth | `quantum-orchestration-auth` | `read` |
| auth-computer | `quantum-orchestration-auth-computer` | `write` |
| gateway | `quantum-orchestration-gateway` | `write` |
| job-orchestrator | `quantum-orchestration-job` | `write` |
| sdk | `quantum-orchestration-sdk` | `write` |
| setup-a | `quantum-setup-nv-frontend` | `write` |
| setup-a-be | `quantum-setup-nv-backend` | `write` |
| setup-registry | `quantum-setup-registry` | `write` |
| shell | `quantum-orchestration-shell` | `write` |
| ui | `quantum-orchestration-ui` | `read` |

모두 `.github/workflows/dev-nks.yml`을 추가했습니다. `push: dev`와 수동 실행만 받고 `github.ref == 'refs/heads/dev' && vars.NKS_DEV_CI_ENABLED == 'true'`일 때만 `nks-reusable.yml@dev`에 `deployment_environment: dev`를 전달합니다. `contents: read`, 기존 packages 권한, `nks-dev-${{ github.repository }}` concurrency, `cancel-in-progress: false`를 사용합니다. `prod-nks.yml`은 보존했습니다. 초기 신규 dev CI 11개는 모두 성공했습니다. 병행 실행되는 legacy CI에서는 auth `dcb62f1`, setup-registry `d0e8e40`을 dev에 후속 반영하여 같은 core source의 격리 MavenLocal 의존성을 사용하고 QC dev에서 중복 package/FAB 배포를 차단했습니다. 이 두 수정의 후속 CI/fanout은 검증 중입니다. 저장된 후속 관찰에서는 registry Build Test가 성공하고 FAB 배포 단계가 skip됐으며, auth Publish Auth Core도 skip됐습니다.

core의 원격 `dev` 생성도 완료됐습니다(초기 반영 `5a449ed`). 다른 10개 저장소와 Helm도 필요한 prod 수정과 dev caller/DNS 설정을 포함해 `dev`에 반영됐습니다. 원격 `prod` 12개 HEAD는 root의 독립 비교에서 이전 값과 같았습니다. 이후 작업도 원격 `prod`를 변경하지 않습니다.

공통 라이브러리는 자기 이미지를 만들지 않더라도 같은 환경의 소비 저장소를 fanout합니다. dev source snapshot, tooling ref, stale 검사, 이미지 `dev-` 태그, writeback branch/path, fanout `dev-nks.yml@dev`가 하나의 환경 계약을 따릅니다. 임의 branch/namespace/URL 입력은 받지 않으며 CI 환경과 snapshot/release 환경이 다르면 네트워크 쓰기 전에 거부합니다. namespace 문자열에 `prod`가 포함되는지는 환경 판별 기준이 아닙니다.

## CI 설정과 기존 Secret 이름

모든 11개 repo에서 source `dev` 및 Helm `dev` 준비 후 `NKS_DEV_CI_ENABLED=true` 설정을 완료했고 초기 CI 11개 성공을 확인했습니다. reusable workflow는 기존 prod caller를 위해 환경 입력 기본값을 `prod`로 유지합니다.

| 이름 | 용도 |
|---|---|
| `NKS_SOURCE_TOKEN` | 승인된 private dependency 소스 조회와 같은 환경 소비 repo fanout dispatch |
| `NKS_GITOPS_TOKEN` | Helm의 환경별 image values writeback |
| `NKS_PACKAGE_READ_TOKEN` | 기존 private Maven package 읽기 |
| `GITHUB_TOKEN` | caller repo의 허용된 GHCR 패키지 발행; 별도 신규 Secret 불필요 |

새 Secret 이름을 추가하지 않습니다. 초기 dev CI 11개 성공은 해당 실행에 필요한 기존 Secret 계약의 동작 근거입니다. 조직 전체의 Secret 제공 정책을 보증하지는 않습니다. 비밀번호·토큰·개인키를 문서에 저장하지 않습니다. Kubernetes `qc-prod-app-secrets`와 GitHub CI Secret은 서로 다른 계약입니다.

## DNS, edge와 TLS

두 dev A 레코드는 모두 `161.122.93.243`으로 전환됐고 시스템 DNS/Google DNS/Cloudflare DoH에서 확인했습니다. 초기 잘못된 NKS 주소와 TLS 불일치 문제는 현재 상태가 아닙니다.

공유 `gateway-system/main-gateway`의 실제 owner는 별도 `argocd/gateway` Application입니다. 이 Application의 `spec.source.kustomize.patches[1].patch` 안에서 `/spec/listeners` 전체를 replace하므로 Gateway 직접 patch나 공유 repo 파일만 수정하면 selfHeal로 되돌아갑니다. root는 기존 listener 7개를 그대로 보존하고 같은 Application override에 `qc-dev-shell-https`와 `qc-dev-modules-https`를 append했습니다. 새 listener는 별도 `gateway-system/qc-dev-tls` Secret을 참조하며 기존 `qc-shell-https`/`qc-modules-https`와 `qc-tls`는 변경하지 않았습니다.

2026-10-24 root는 별도 `qc-dev-tls` Certificate를 `letsencrypt-prod` ClusterIssuer의 DNS01로 발급하여 `Ready=True`를 확인했습니다. HAProxy도 기존 host를 보존한 dev SNI/HTTP redirect 추가본으로 백업·검증·reload했습니다. 이후 dev Route 연결을 완료했고 공개 HTTPS와 로그인은 200, 대시보드 Playwright도 성공했습니다. 초기 Route 미연결 시점의 HTTP 404는 해소됐습니다. 이 라이브 결과는 이번 executor의 로컬 수정/정적 검증과 별개입니다. 근거는 작업 증거 디렉터리 `.codex-runs/dev-cicd-20261024/`의 `dev-dns-onprem-recheck.json`, `onprem-dev-certificate.json`, `public-edge-applied.json`, `dev-auth-assets-smoke.json`, `post-rollout-independent-verification.json`입니다.

[운영 예시](../infra/public-edge/examples/REVIEW-NOTES.txt)는 root가 적용한 Application override patch와 별도 Certificate를 기준으로 합니다. 이미 적용한 예시를 재적용하지 않습니다. 향후 변경 시 최신 Application의 `resourceVersion`, patch target와 patch 원문을 다시 조회하고 root 검토 후 진행합니다. NKS `infra/nks-public-edge` 소유권은 변경하지 않습니다.

사용자 명시 승인 후 root가 활성 manifest/version id `55`의 두 행에서 URL·direct API·connect-src 도메인을 dev origin으로 전환했습니다. 조건 guard=1 및 update rows=1/1을 확인하고 COMMIT했으며 버전·해시·trust·실험 데이터는 보존했습니다(`onprem-module-dev-url-applied.txt`). 독립 GET에서 manifest/asset 200과 dev CSP를 확인했습니다(`module-dev-url-independent-check.json`). setup scoped token 교환 및 setupBE permissions/NV 목록도 200입니다. 다만 module jobs 목록은 404이며, 배포된 job-orchestrator에 delegated `/internal/setups/{id}/jobs` API가 없는 것을 확인했습니다. delegated submit에 필요한 Registry 내부 setup 메타 GET도 미배포여서 두 서버의 계약을 함께 복구해야 합니다. 이 bridge의 clean clone 수정·배포와 새 dev 모듈 upload/activate, iframe 기능 회귀는 아래 후속 작업입니다. platform token을 iframe에 넘기는 우회는 사용하지 않습니다.

## 남은 활성화와 검증 순서

11개 dev branch/caller/enable과 초기 CI, DNS/TLS/HAProxy/Route, 앱 7개 digest 검증, DB 두 행의 도메인 전환은 완료된 작업입니다. 재실행하거나 DB bootstrap/restore·계정 초기화를 하지 않습니다. 작업 증거는 `.codex-runs/dev-cicd-20261024/`의 `published-commits.json`, `actions-live-monitor.json`, `post-rollout-independent-verification.json`, `dev-job-history-check.json`, `onprem-module-dev-url-applied.txt`에 있습니다. legacy CI 후속 확인은 `legacy-ci-published.json`과 `actions-followup-monitor.json`을 사용합니다.

1. root가 Job delegated API 4개와 Registry 내부 setup 메타 GET의 최소 이식 결과를 검토·dev 반영합니다. Registry의 기존 read audience/principal 및 Job의 기존 event ingest 계약을 유지합니다. 원본 dirty 작업트리는 보존하고 `prod`는 변경하지 않습니다. Registry CI/Ready 이후 Job CI/Ready 순서로 검증하며 dependency fanout과 같은 서비스의 중복 build/publish를 피합니다.
2. root가 GET-only 라이브 확인으로 Registry 내부 메타 조회, Job mapping 4경로 등록, 같은 scoped exchange를 거친 module jobs 목록/기존 detail 200 및 기존 permissions/NV 200을 확인합니다. 실 submit/cancel이나 Kafka 발행으로 검증하지 않습니다. 기존 Job 162건과 측정 데이터를 보존합니다.
3. 새 dev `modulePublisher` image/version 및 bundle/runtime 환경 프로비넌스는 확인됐습니다. backend 준비 후 root가 **온프레미스 `argocd/qc-prod-app.yaml` 하나만** 적용하여 상위 publisher 정지 parameter가 제거됐는지 확인합니다. 환경별 values가 게시 여부를 결정하며 dev `PostSync` Job `qc-nv-module-publish`의 upload/activate 결과는 별도로 확인해야 합니다. 이 문서의 로컬 변경만으로 게시 완료를 의미하지 않습니다. NKS prod 정지 parameter와 stateful 데이터 구조는 유지합니다.
4. root가 브라우저 로그인, 별도 origin iframe, setup scoped token, 모듈 직접 API, 기존 Job/측정 목록과 인터랙티브 5기능 회귀를 검증합니다. auth `dcb62f1`/registry `d0e8e40`의 후속 CI/fanout 결과도 함께 확인합니다.

보드 transient worker는 23:29 KST 두 번의 독립 관찰에서 active/running, Kafka group STABLE/member 1, start lag 0이었습니다. 기존 topic offset도 같고 새 실험은 발행하지 않았습니다(`board-running-verification.json`). 재부팅 자동기동이나 실제 보드 실험 E2E까지 완료했다는 뜻은 아닙니다.

## 검증과 API 호출 예시

저장소 정적 검증:

```bash
python3 -m unittest discover -s ci/nks/tests -v
./scripts/render-check.sh
git diff --check
```

정적 테스트는 환경 혼합 거부, dev writeback/fanout, 이미지·Argo values·namespace 계약, 기존 prod 렌더 보존을 확인합니다. 온프레미스 렌더 비교는 origin·image와 의도된 두 sectionName 변경만 허용하고 dev Route가 dev listener를 참조하는지 확인합니다. publisher off/on을 모두 검증하며, on에서 추가되는 `qc-nv-module-publish` Job 한 개만 이름·PostSync·dev 환경·namespace·기존 Secret·허용 publisher·불변 digest를 명시 검증한 뒤 보존 비교에서 제외합니다. 나머지 모든 리소스, 기존 7개 앱, namespace와 스토리지 비교는 유지합니다. DNS/TLS와 초기 앱 배포 검증은 완료됐고, 이번 bridge 배포 및 module upload/activate·E2E는 root의 후속 라이브 검증 대상입니다.

아래는 담당자가 활성화 후 실행할 예시이며 이번 로컬 구현에서는 실행하지 않았습니다. 저장된 `gh` 인증을 사용하고 토큰을 출력하지 않습니다.

```bash
# 읽기: dev workflow 실행 결과 조회
env -u GITHUB_TOKEN -u GH_TOKEN gh api \
  'repos/quantum-platform-kist/quantum-orchestration-shell/actions/workflows/dev-nks.yml/runs?branch=dev&per_page=5' \
  --jq '.workflow_runs[] | {id, head_branch, head_sha, status, conclusion, html_url}'

# 쓰기: 전체 branch/variable/Secret 제공 준비 후 운영 담당만 수동 실행
env -u GITHUB_TOKEN -u GH_TOKEN gh api --method POST \
  repos/quantum-platform-kist/quantum-orchestration-shell/actions/workflows/dev-nks.yml/dispatches \
  -f ref=dev

# 읽기: 인증서 검증을 유지하는 온프레미스 HTTPS 재확인
curl --fail --silent --show-error --output /dev/null \
  --resolve qc-dev.quantum-instrument.com:443:161.122.93.243 \
  https://qc-dev.quantum-instrument.com/health
curl --fail --silent --show-error --output /dev/null \
  --resolve modules.qc-dev.quantum-instrument.com:443:161.122.93.243 \
  https://modules.qc-dev.quantum-instrument.com/health
```

로그인 자격증명이나 관리자 JWT를 명령 예시에 넣지 않습니다. 인증된 module upload/activate는 검증된 dev publisher가 기존 Secret을 Pod 환경으로 받아 수행하며, 새 환경 계약을 통과한 bundle만 게시합니다.
