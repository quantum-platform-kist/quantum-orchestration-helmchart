#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CONFIG_SRC="${SRC_ROOT}/haproxy.cfg"
HAPROXY_IMAGE="${HAPROXY_IMAGE:-haproxy:2.8-alpine}"
TLS_PORT="${TLS_PORT:-10443}"
HTTP_PORT="${HTTP_PORT:-10080}"
BACKEND_PORT="${BACKEND_PORT:-18443}"

TMP_DIR=""
BACKEND_PID=""
HAPROXY_CID=""

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  set +e
  if [ -n "${HAPROXY_CID}" ]; then
    docker rm -f "${HAPROXY_CID}" >/dev/null 2>&1
  fi
  if [ -n "${BACKEND_PID}" ]; then
    kill "${BACKEND_PID}" >/dev/null 2>&1
    wait "${BACKEND_PID}" 2>/dev/null
  fi
  if [ -n "${TMP_DIR}" ]; then
    rm -rf "${TMP_DIR}"
  fi
}

wait_for_port() {
  local port="$1"
  local label="$2"
  local i
  for i in $(seq 1 80); do
    if bash -c ":</dev/tcp/127.0.0.1/${port}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done
  die "timeout waiting for ${label} on port ${port}"
}

wait_for_haproxy_port() {
  local port="$1"
  local label="$2"
  local i
  for i in $(seq 1 80); do
    if bash -c ":</dev/tcp/127.0.0.1/${port}" >/dev/null 2>&1; then
      return 0
    fi
    if [ -n "${HAPROXY_CID}" ] && [ "$(docker inspect -f '{{.State.Running}}' "${HAPROXY_CID}" 2>/dev/null || true)" != "true" ]; then
      docker logs "${HAPROXY_CID}" >&2 || true
      die "HAProxy container exited before ${label} opened port ${port}"
    fi
    sleep 0.1
  done
  docker logs "${HAPROXY_CID}" >&2 || true
  die "timeout waiting for ${label} on port ${port}"
}

tls_must_pass() {
  local sni="$1"
  local out="${TMP_DIR}/tls-${sni}.log"
  if ! printf 'GET / HTTP/1.0\r\nHost: %s\r\n\r\n' "${sni}" \
    | timeout 8 openssl s_client -connect "127.0.0.1:${TLS_PORT}" -servername "${sni}" -quiet >"${out}" 2>&1; then
    cat "${out}" >&2
    docker logs "${HAPROXY_CID}" >&2 || true
    die "TLS passthrough failed for ${sni}"
  fi
  printf 'TLS pass OK: %s\n' "${sni}"
}

tls_must_block() {
  local label="$1"
  shift
  local out="${TMP_DIR}/tls-block-${label}.log"
  if printf 'GET / HTTP/1.0\r\n\r\n' \
    | timeout 8 openssl s_client -connect "127.0.0.1:${TLS_PORT}" "$@" -quiet >"${out}" 2>&1; then
    cat "${out}" >&2
    die "TLS block failed for ${label}"
  fi
  printf 'TLS block OK: %s\n' "${label}"
}

http_code() {
  local host="$1"
  curl -sS -o /dev/null -w '%{http_code}' -H "Host: ${host}" "http://127.0.0.1:${HTTP_PORT}/probe?x=1"
}

trap cleanup EXIT

[ -f "${CONFIG_SRC}" ] || die "config not found: ${CONFIG_SRC}"
command -v docker >/dev/null 2>&1 || die "docker is missing"
command -v openssl >/dev/null 2>&1 || die "openssl is missing"
command -v curl >/dev/null 2>&1 || die "curl is missing"

TMP_DIR="$(mktemp -d)"
mkdir -p "${TMP_DIR}/run"

sed \
  -e "s/bind 0.0.0.0:443/bind 0.0.0.0:${TLS_PORT}/" \
  -e "s/bind 0.0.0.0:80/bind 0.0.0.0:${HTTP_PORT}/" \
  -e "s/10[.]42[.]2[.]2:443/127.0.0.1:${BACKEND_PORT}/" \
  -e 's#log /dev/log local0#log stdout format raw local0#' \
  -e '/log \/dev\/log local1 notice/d' \
  -e '/option tcp-check/d' \
  -e '/tcp-check connect/d' \
  -e 's/ check inter 5s rise 2 fall 3//' \
  "${CONFIG_SRC}" >"${TMP_DIR}/haproxy.cfg"

openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj "/CN=local-qc-public-edge-test" \
  -keyout "${TMP_DIR}/backend.key" \
  -out "${TMP_DIR}/backend.crt" >/dev/null 2>&1

openssl s_server -quiet -accept "${BACKEND_PORT}" \
  -cert "${TMP_DIR}/backend.crt" \
  -key "${TMP_DIR}/backend.key" \
  -www >"${TMP_DIR}/backend.log" 2>&1 &
BACKEND_PID="$!"
sleep 0.5
if ! kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
  cat "${TMP_DIR}/backend.log" >&2 || true
  die "test TLS backend failed to start"
fi

docker run --rm \
  -v "${TMP_DIR}/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro" \
  -v "${TMP_DIR}/run:/run/qc-public-edge" \
  "${HAPROXY_IMAGE}" \
  haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg

HAPROXY_CID="$(
  docker run -d --network host \
    --user root \
    -v "${TMP_DIR}/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro" \
    -v "${TMP_DIR}/run:/run/qc-public-edge" \
    "${HAPROXY_IMAGE}" \
    haproxy -db -f /usr/local/etc/haproxy/haproxy.cfg
)"
wait_for_haproxy_port "${TLS_PORT}" "HAProxy TLS frontend"
wait_for_haproxy_port "${HTTP_PORT}" "HAProxy HTTP frontend"

tls_must_pass "qc.quantum-instrument.com"
tls_must_pass "modules.qc.quantum-instrument.com"
tls_must_pass "qc-dev.quantum-instrument.com"
tls_must_pass "modules.qc-dev.quantum-instrument.com"
tls_must_block "unknown-sni" -servername "argocd.quantum-instrument.com"
tls_must_block "absent-sni"

code="$(http_code "qc.quantum-instrument.com")"
[ "${code}" = "308" ] || die "expected HTTP 308 for qc host, got ${code}"
printf 'HTTP redirect OK: qc.quantum-instrument.com -> %s\n' "${code}"

code="$(http_code "modules.qc.quantum-instrument.com")"
[ "${code}" = "308" ] || die "expected HTTP 308 for modules host, got ${code}"
printf 'HTTP redirect OK: modules.qc.quantum-instrument.com -> %s\n' "${code}"

for host in qc-dev.quantum-instrument.com modules.qc-dev.quantum-instrument.com; do
  for host_header in "${host}" "${host}:80"; do
    code="$(http_code "${host_header}")"
    [ "${code}" = "308" ] || die "expected HTTP 308 for ${host_header}, got ${code}"
    redirect="$(curl -sS -o /dev/null -w '%{redirect_url}' -H "Host: ${host_header}" "http://127.0.0.1:${HTTP_PORT}/probe?x=1")"
    [ "${redirect}" = "https://${host_header}/probe?x=1" ] || die "unexpected redirect for ${host_header}: ${redirect}"
    printf 'HTTP redirect OK: %s -> %s\n' "${host_header}" "${code}"
  done
done

code="$(http_code "argocd.quantum-instrument.com")"
[ "${code}" = "421" ] || die "expected HTTP 421 for unknown host, got ${code}"
printf 'HTTP reject OK: argocd.quantum-instrument.com -> %s\n' "${code}"

printf 'local docker verification complete with %s\n' "${HAPROXY_IMAGE}"
