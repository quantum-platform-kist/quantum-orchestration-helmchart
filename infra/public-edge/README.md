# QC Public Edge

This directory contains the public-edge HAProxy files for `161.122.93.243`.
It only allows the Quantum Cloud hosts:

- `qc.quantum-instrument.com`
- `modules.qc.quantum-instrument.com`

TLS is passed through by SNI to the Cilium Gateway VIP `10.42.2.2:443`.
Certificates are issued and terminated inside the Kubernetes Gateway, not on
this public edge host.

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

Run on `161.122.93.243` only after the parent task installs the HAProxy package
and confirms no owner conflict for `80`/`443`:

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

```bash
sudo haproxy -c -f /etc/quantum-cloud/haproxy.cfg
sudo systemctl --no-pager --lines=50 status qc-public-edge.service
openssl s_client -connect 161.122.93.243:443 -servername qc.quantum-instrument.com -brief </dev/null
openssl s_client -connect 161.122.93.243:443 -servername modules.qc.quantum-instrument.com -brief </dev/null
! openssl s_client -connect 161.122.93.243:443 -servername argocd.quantum-instrument.com -brief </dev/null
! openssl s_client -connect 161.122.93.243:443 -brief </dev/null
curl -I -H 'Host: qc.quantum-instrument.com' http://161.122.93.243/
curl -I -H 'Host: modules.qc.quantum-instrument.com' http://161.122.93.243/
curl -I -H 'Host: argocd.quantum-instrument.com' http://161.122.93.243/
```

Expected results:

- The two QC TLS SNI checks complete a TLS handshake against the Kubernetes Gateway certificate.
- Unknown and absent SNI are closed before reaching the Gateway.
- QC HTTP hosts return `308` to HTTPS.
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
