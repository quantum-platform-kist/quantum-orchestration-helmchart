# QC Public Edge

This directory contains the public-edge HAProxy files for `161.122.93.243`.
This is the existing on-premises dev environment, separate from NKS prod.
It only allows the Quantum Cloud hosts:

- `qc.quantum-instrument.com`
- `modules.qc.quantum-instrument.com`
- `qc-dev.quantum-instrument.com`
- `modules.qc-dev.quantum-instrument.com`

TLS is passed through by SNI to the Cilium Gateway VIP `10.42.2.2:443`.
Certificates are issued and terminated inside the Kubernetes Gateway, not on
this public edge host.

[검토 메모](examples/REVIEW-NOTES.txt)는 DNS 정정, 별도 `qc-dev-tls` Certificate와
공유 Gateway Application override를 설명합니다. 실제 owner는 `argocd/gateway`
Application의 `spec.source.kustomize.patches[1].patch`이며 `/spec/listeners` 전체를
replace합니다. Gateway 직접 patch나 공유 repo 파일만 수정하면 selfHeal로 되돌아갑니다.

2026-10-24 root는 기존 7개 listener와 `qc-tls`를 그대로 보존하고
`qc-dev-shell-https`/`qc-dev-modules-https`를 같은 Application override에 추가했습니다.
새 listener가 참조하는 별도 `qc-dev-tls`는 DNS01 발급 후 `Ready=True`이며,
HAProxy도 사용자 sudo 승인 후 백업·검증·reload했습니다. `161.122.93.243`으로
resolve한 dev TLS 검증은 성공했고 당시 dev Route가 없어 HTTP 404였습니다.
이 내용은 root의 라이브 확인 결과이며 이번 executor는 로컬 문서/예시만 수정했습니다.

[Application patch 예시](examples/onprem-dev-gateway-application.jsonpatch.example)와
[Certificate 예시](examples/onprem-qc-dev-tls-certificate.yaml.example)는 적용 이력에
기반한 검토 자료입니다. 이미 적용된 예시를 재적용하지 않습니다. 향후 변경은 최신
Application의 `resourceVersion`, target, patch 원문을 다시 조회하고 root 검토 후 진행합니다.
남은 작업은 DNS 정정/전파 확인과 `qc-prod-app` sync를 통한 dev Route 연결/앱 검증입니다.

## Files

- `haproxy.cfg`: TCP SNI passthrough on `443` and HTTP redirect policy on `80`.
- `qc-public-edge.service`: separate systemd service using `/etc/quantum-cloud/haproxy.cfg`.
- `scripts/install-qc-public-edge.sh`: root install/start flow with review gate for existing `80`/`443` listeners.
- `scripts/reload-qc-public-edge.sh`: validate, backup, install optional replacement config, then reload.
- `scripts/verify-local-docker.sh`: local Docker check for config syntax, allowed SNI, blocked SNI, and HTTP host policy.

## Safety Model

- Do not edit `/etc/haproxy/haproxy.cfg`.
- Do not modify `haproxy.service`.
- If another process already listens on `80` or `443`, the installer exits with a review gate.
- Existing `/etc/quantum-cloud/haproxy.cfg`, service unit, and installed README are backed up under `/var/backups/quantum-cloud/`.
- Non-QC SNI, absent SNI, and non-QC HTTP hosts are rejected. This includes Argo, Dashboard, and FAB hostnames.
- Existing NetworkManager/nft NAT sharing is not changed by these files.

## Local Verification

From the repo root:

```bash
cd quantum-orchestration-helmchart/infra/public-edge
./scripts/verify-local-docker.sh
```

The script uses `haproxy:2.8-alpine` by default. To use another image:

```bash
HAPROXY_IMAGE=haproxy:2.9-alpine ./scripts/verify-local-docker.sh
```

If local test ports are already in use:

```bash
TLS_PORT=20443 HTTP_PORT=20080 BACKEND_PORT=28443 ./scripts/verify-local-docker.sh
```

## Parent Apply Commands

다음은 향후 설치/설정 변경 시의 운영 명령 참고입니다. 2026-10-24 root가 이미
백업·검증·reload했으므로 이번 문서 정정 때문에 재실행하지 않습니다.
새로 설치할 경우에만 담당자가 `161.122.93.243`의 패키지와 `80`/`443` 소유권을 확인합니다:

```bash
cd /path/to/quantum-orchestration-helmchart/infra/public-edge
sudo ./scripts/install-qc-public-edge.sh
```

For a config-only update:

```bash
cd /path/to/quantum-orchestration-helmchart/infra/public-edge
sudo ./scripts/reload-qc-public-edge.sh ./haproxy.cfg
```

## Post-Apply Checks

root가 별도 `qc-dev-tls` 발급과 Application dev listener 추가를 완료했습니다.
아래는 담당자의 후속 확인 명령이며 이번 executor는 실행하지 않았습니다.
TLS 검증 성공과 실제 앱 응답을 구분합니다. dev Route sync 전 HTTP 404는 예상 결과입니다:

```bash
sudo haproxy -c -f /etc/quantum-cloud/haproxy.cfg
sudo systemctl --no-pager --lines=50 status qc-public-edge.service
openssl s_client -connect 161.122.93.243:443 -servername qc-dev.quantum-instrument.com -verify_hostname qc-dev.quantum-instrument.com -verify_return_error -brief </dev/null
openssl s_client -connect 161.122.93.243:443 -servername modules.qc-dev.quantum-instrument.com -verify_hostname modules.qc-dev.quantum-instrument.com -verify_return_error -brief </dev/null
! openssl s_client -connect 161.122.93.243:443 -servername argocd.quantum-instrument.com -brief </dev/null
! openssl s_client -connect 161.122.93.243:443 -brief </dev/null
curl -I -H 'Host: qc.quantum-instrument.com' http://161.122.93.243/
curl -I -H 'Host: modules.qc.quantum-instrument.com' http://161.122.93.243/
curl -I -H 'Host: qc-dev.quantum-instrument.com' http://161.122.93.243/
curl -I -H 'Host: modules.qc-dev.quantum-instrument.com' http://161.122.93.243/
curl -I -H 'Host: argocd.quantum-instrument.com' http://161.122.93.243/
```

Expected results:

- The two dev TLS SNI checks complete a TLS handshake and verify the hostname against the Kubernetes Gateway certificate.
- Unknown and absent SNI are closed before reaching the Gateway.
- All four allowed QC HTTP hosts return `308` to HTTPS.
- Other HTTP hosts return `421`.

## Rollback

Stop only the QC-specific service:

```bash
sudo systemctl disable --now qc-public-edge.service
```

Restore the previous QC config if one exists:

```bash
sudo cp -a /var/backups/quantum-cloud/haproxy.cfg.<timestamp>.bak /etc/quantum-cloud/haproxy.cfg
sudo haproxy -c -f /etc/quantum-cloud/haproxy.cfg
sudo systemctl start qc-public-edge.service
```

If the unit itself must be rolled back:

```bash
sudo cp -a /var/backups/quantum-cloud/qc-public-edge.service.<timestamp>.bak /etc/systemd/system/qc-public-edge.service
sudo systemctl daemon-reload
sudo systemctl restart qc-public-edge.service
```
