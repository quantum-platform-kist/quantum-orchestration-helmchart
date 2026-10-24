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

온프레미스 실측 context는 `kubernetes-admin@prod-k8s`입니다. `qc-prod-app`과 `qc-prod-stateful`은 이미 `dev`를 추적하며, 조사 시 `f97407f`에서 Synced/Healthy였습니다. 이 구성은 새 클러스터 설치가 아닙니다. 기존 DB/Job/측정 데이터, Secret 값, PVC, 노드 `prod-k8s-02/03`, StorageClass `fab-lvm-retain`, DNS resolver `10.96.0.10`, trusted proxy `10.244.2.0/24`를 유지합니다. 새 namespace·NAS·S3 bucket을 준비할 필요가 없습니다.

`qc-prod-app.yaml`의 values 순서는 기존 `prod-app` → `prod-app-images` → `prod-app-online` → `dev-dns` → `onprem-dev-images` 8종입니다. `valuesObject.global.gateway`의 `shellSectionName: qc-dev-shell-https`, `moduleSectionName: qc-dev-modules-https`로 HTTPRoute를 dev listener에 연결합니다. 기존 Route 이름·backend·namespace와 `gateway-system/main-gateway` 부모는 유지합니다. `valuesObject.global.deploymentEnvironment=dev`를 지정하고 `modulePublish.enabled=false`를 상위 parameter로 고정합니다. 앱 자동 동기화는 `selfHeal=true`, `prune=false`로 준비하며 실제 적용 시점은 운영 담당이 결정합니다. `qc-prod-stateful.yaml`의 현재 구조와 수동 동기화는 유지합니다. NKS prod app의 Registry 정지/publisher 정지 parameters, 이미지 digest와 edge 소유권도 보존합니다.

## 11개 앱 저장소의 브랜치와 caller

각 저장소의 clean clone은 `.codex-runs/dev-cicd-20261024/repos/<alias>`에 있으며 `origin/prod`에서 시작한 로컬 `codex/dev-cicd`입니다. 원본 개발 작업트리의 사용자 변경은 가져오지 않았습니다. 기존 prod에는 빌드 의존성·서비스 신원/토큰 계약·모듈 manifest 수정이 있으므로 이 기준을 포함해 dev에 통합합니다. 제품 코드를 다시 작성하거나 prod 수정만 제외하고 caller를 옮기지 않습니다.

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

모두 `.github/workflows/dev-nks.yml`을 추가합니다. `push: dev`와 수동 실행만 받고 `github.ref == 'refs/heads/dev' && vars.NKS_DEV_CI_ENABLED == 'true'`일 때만 `nks-reusable.yml@dev`에 `deployment_environment: dev`를 전달합니다. `contents: read`, 기존 packages 권한, `nks-dev-${{ github.repository }}` concurrency, `cancel-in-progress: false`를 사용합니다. `prod-nks.yml`과 기존 BuildTest/Publish workflow는 그대로입니다. 기존 dev push workflow도 병행 실행될 수 있으므로 첫 통합 때 해당 실행 결과를 함께 확인합니다.

core만 원격 `dev`가 없고 기존 개발 기준이 `main`입니다. 운영 담당이 prod 수정이 포함된 core `dev`를 먼저 생성해야 합니다. 다른 10개는 기존 `dev`가 `prod`의 조상이므로 prod 수정과 caller를 포함한 `codex/dev-cicd` → `dev` 통합 대상으로 준비합니다. Helm도 prod 기준에 기존 dev DNS 변경을 통합한 작업 브랜치를 `dev`로 반영합니다. 이 절차에서 원격 `prod`는 변경하지 않습니다.

공통 라이브러리는 자기 이미지를 만들지 않더라도 같은 환경의 소비 저장소를 fanout합니다. dev source snapshot, tooling ref, stale 검사, 이미지 `dev-` 태그, writeback branch/path, fanout `dev-nks.yml@dev`가 하나의 환경 계약을 따릅니다. 임의 branch/namespace/URL 입력은 받지 않으며 CI 환경과 snapshot/release 환경이 다르면 네트워크 쓰기 전에 거부합니다. namespace 문자열에 `prod`가 포함되는지는 환경 판별 기준이 아닙니다.

## CI 설정과 기존 Secret 이름

모든 11개 repo에서 `NKS_DEV_CI_ENABLED`는 첫 준비 중 비활성으로 두고 전체 source `dev` 및 Helm `dev`가 준비된 뒤 문자열 `true`로 설정합니다. reusable workflow는 기존 prod caller를 위해 환경 입력 기본값을 `prod`로 유지합니다.

| 이름 | 용도 |
|---|---|
| `NKS_SOURCE_TOKEN` | 승인된 private dependency 소스 조회와 같은 환경 소비 repo fanout dispatch |
| `NKS_GITOPS_TOKEN` | Helm의 환경별 image values writeback |
| `NKS_PACKAGE_READ_TOKEN` | 기존 private Maven package 읽기 |
| `GITHUB_TOKEN` | caller repo의 허용된 GHCR 패키지 발행; 별도 신규 Secret 불필요 |

새 Secret 이름을 추가하지 않습니다. 현재 prod 실행 성공은 기존 계약의 동작 근거지만 조직 Secret의 선택 repo 정책까지 확인된 것은 아닙니다. 운영 담당이 기존 세 이름의 repo 제공 범위와 권한을 확인합니다. 이 작업은 Secret 값을 조회·복사하지 않았고 비밀번호·토큰·개인키를 문서에 저장하지 않습니다. Kubernetes `qc-prod-app-secrets`와 GitHub CI Secret은 서로 다른 계약입니다.

## DNS, edge와 TLS

두 dev A 레코드는 `161.122.93.243`이어야 합니다. 조사 당시 `175.45.220.187`은 잘못된 NKS 주소였고, 그 주소의 `*.isusystem.com` 인증서는 dev host와 일치하지 않았습니다. DNS 정정/전파 확인은 온프레미스 TLS 검증과 구분합니다.

공유 `gateway-system/main-gateway`의 실제 owner는 별도 `argocd/gateway` Application입니다. 이 Application의 `spec.source.kustomize.patches[1].patch` 안에서 `/spec/listeners` 전체를 replace하므로 Gateway 직접 patch나 공유 repo 파일만 수정하면 selfHeal로 되돌아갑니다. root는 기존 listener 7개를 그대로 보존하고 같은 Application override에 `qc-dev-shell-https`와 `qc-dev-modules-https`를 append했습니다. 새 listener는 별도 `gateway-system/qc-dev-tls` Secret을 참조하며 기존 `qc-shell-https`/`qc-modules-https`와 `qc-tls`는 변경하지 않았습니다.

2026-10-24 root는 별도 `qc-dev-tls` Certificate를 `letsencrypt-prod` ClusterIssuer의 DNS01로 발급하여 `Ready=True`를 확인했습니다. HAProxy도 기존 host를 보존한 dev SNI/HTTP redirect 추가본으로 백업·검증·reload했습니다. root가 `161.122.93.243`으로 resolve하여 확인한 dev TLS는 검증에 성공했고, 당시 dev Route가 아직 없어 HTTP 404였습니다. 이 라이브 결과는 이번 executor의 로컬 수정/정적 검증과 별개입니다. 근거는 작업 증거 디렉터리 `.codex-runs/dev-cicd-20261024/`의 `onprem-acme-gateway.json`, `gateway-application-before-dev.json`, `gateway-application-dev-listeners.jsonpatch`, `onprem-dev-certificate.json`, `public-edge-applied.json`입니다. Certificate `Ready=True`와 TLS/404 결과는 root의 라이브 확인 보고이며 Certificate manifest 자체는 상태 증명이 아닙니다.

[운영 예시](../infra/public-edge/examples/REVIEW-NOTES.txt)는 root가 적용한 Application override patch와 별도 Certificate를 기준으로 합니다. 이미 적용한 예시를 재적용하지 않습니다. 향후 변경 시 최신 Application의 `resourceVersion`, patch target와 patch 원문을 다시 조회하고 root 검토 후 진행합니다. 남은 작업은 DNS 정정/전파 확인, `qc-prod-app` sync로 새 sectionName을 사용하는 dev Route 연결, 실제 앱/모듈 검증입니다. NKS `infra/nks-public-edge` 소유권은 변경하지 않습니다.

## 실제 활성화 순서

1. 운영 담당이 11개 source branch와 Helm 변경을 검토·반영합니다. core `dev`를 먼저 만들고, 다른 모든 의존 repo의 dev도 prod 필수 수정을 포함한 상태로 맞춥니다. 아직 CI enable variable은 켜지 않습니다.
2. 기존 온프레미스 context/Application/데이터 서비스/PVC 상태를 재확인합니다. 기존 app Secret의 필요한 key 제공 여부, 셋업 `stp_nv_a1`과 서비스 신원 호환성을 확인합니다. 기존 DB bootstrap/restore를 재실행하거나 계정/비밀번호를 초기화하지 않습니다.
3. dev DNS의 올바른 IP 반영/전파를 확인합니다. root가 완료한 별도 `qc-dev-tls` 발급, HAProxy 반영, dev listener 추가 상태를 확인하며 예시를 재적용하지 않습니다. 이미 활성화된 모듈 manifest의 URL·direct API·connect-src도 dev origin으로 전환할 준비가 필요합니다.
4. source repo의 `NKS_DEV_CI_ENABLED=true`를 설정한 뒤 필요한 build를 실행합니다. 공통 core/auth/ui와 생산자 dependency가 모두 준비된 상태에서 fanout 결과까지 확인합니다. GitOps 결과는 Helm `dev`의 `values/onprem-dev-images/`에만 기록되어야 합니다.
5. **온프레미스에서 `argocd/qc-prod-app.yaml` 하나를 선택**해 검토·적용합니다. `argocd/` 전체 적용과 NKS Application 선택은 하지 않습니다. stateful 데이터 구조는 유지합니다. 새 app source가 dev DNS와 8종 image values를 읽고 HTTPRoute가 `qc-dev-shell-https`/`qc-dev-modules-https`에 연결되는지 확인합니다. 이어 기존 7개 앱의 readiness·서비스 신원을 검증합니다.
6. 초기 8개 digest는 존재가 검증된 기존 prod 이미지의 seed일 수 있고 dev build 완료를 의미하지 않습니다. seed에는 prod `sourceFingerprint`를 두지 않습니다. `modulePublish.enabled=false`를 유지한 채 새 dev `modulePublisher` 이미지가 dev bundle/runtime 환경을 담는지, 셋업과 기존 Secret이 준비됐는지 확인합니다. 준비 후 운영 담당이 상위 정지 parameter를 명시적으로 해제하고 upload/activate 결과를 확인합니다.
7. 브라우저 로그인, 별도 origin iframe, setup scoped token, 모듈 직접 API와 기존 Job/측정 목록을 확인합니다. 보드 실험 실행 워커 기동과 실제 실험은 별도 담당/검증이며 CI green이나 Argo Healthy로 대체하지 않습니다.

## 검증과 API 호출 예시

저장소 정적 검증:

```bash
python3 -m unittest discover -s ci/nks/tests -v
./scripts/render-check.sh
git diff --check
```

정적 테스트는 환경 혼합 거부, dev writeback/fanout, 이미지·Argo values·namespace 계약, 기존 prod 렌더 보존을 확인합니다. 온프레미스 렌더 비교는 origin·image와 의도된 두 sectionName 변경만 허용하고 dev Route가 dev listener를 참조하는지 확인합니다. root의 TLS 확인과 별개로 DNS 전파·Secret 유효성·앱 배포/모듈 활성화 성공은 후속 라이브 검증 대상입니다.

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

# 읽기: DNS 전파 전에도 인증서 검증을 유지하는 온프레미스 HTTPS 확인
curl --fail --silent --show-error --output /dev/null \
  --resolve qc-dev.quantum-instrument.com:443:161.122.93.243 \
  https://qc-dev.quantum-instrument.com/health
curl --fail --silent --show-error --output /dev/null \
  --resolve modules.qc-dev.quantum-instrument.com:443:161.122.93.243 \
  https://modules.qc-dev.quantum-instrument.com/health
```

로그인 자격증명이나 관리자 JWT를 명령 예시에 넣지 않습니다. 인증된 module upload/activate는 검증된 dev publisher가 기존 Secret을 Pod 환경으로 받아 수행하며, 새 환경 계약을 통과한 bundle만 게시합니다.
