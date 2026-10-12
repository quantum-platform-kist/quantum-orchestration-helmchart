#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="qc-public-edge.service"
DEST_DIR="/etc/quantum-cloud"
CONFIG_DEST="${DEST_DIR}/haproxy.cfg"
BACKUP_DIR="/var/backups/quantum-cloud"
CONFIG_SRC="${1:-}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

backup_file() {
  local path="$1"
  local label="$2"
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  if [ -e "${path}" ]; then
    install -d -m 0750 "${BACKUP_DIR}"
    cp -a "${path}" "${BACKUP_DIR}/${label}.${stamp}.bak"
    printf '%s\n' "${BACKUP_DIR}/${label}.${stamp}.bak"
  fi
}

if [ "${EUID}" -ne 0 ]; then
  die "run with sudo/root"
fi

command -v haproxy >/dev/null 2>&1 || die "haproxy binary is missing"
command -v systemctl >/dev/null 2>&1 || die "systemctl is missing"
[ -f "${CONFIG_DEST}" ] || die "installed config not found: ${CONFIG_DEST}"

install -d -m 0755 /run/qc-public-edge

rollback_backup=""
if [ -n "${CONFIG_SRC}" ]; then
  [ -f "${CONFIG_SRC}" ] || die "config not found: ${CONFIG_SRC}"
  haproxy -c -q -f "${CONFIG_SRC}"
  rollback_backup="$(backup_file "${CONFIG_DEST}" "haproxy.cfg")"
  install -m 0644 "${CONFIG_SRC}" "${CONFIG_DEST}"
fi

haproxy -c -q -f "${CONFIG_DEST}"

if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
  die "${SERVICE_NAME} is not active; run install-qc-public-edge.sh first"
fi

if ! systemctl reload "${SERVICE_NAME}"; then
  if [ -n "${rollback_backup}" ] && [ -f "${rollback_backup}" ]; then
    cp -a "${rollback_backup}" "${CONFIG_DEST}"
    haproxy -c -q -f "${CONFIG_DEST}" || true
    systemctl reload "${SERVICE_NAME}" || true
    die "reload failed; previous config restored from ${rollback_backup}"
  fi
  die "reload failed"
fi

systemctl --no-pager --lines=20 status "${SERVICE_NAME}"
