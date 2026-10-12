#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="qc-public-edge.service"
DEST_DIR="/etc/quantum-cloud"
CONFIG_DEST="${DEST_DIR}/haproxy.cfg"
README_DEST="${DEST_DIR}/README.public-edge.md"
UNIT_DEST="/etc/systemd/system/${SERVICE_NAME}"
BACKUP_DIR="/var/backups/quantum-cloud"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CONFIG_SRC="${1:-${SRC_ROOT}/haproxy.cfg}"
UNIT_SRC="${SRC_ROOT}/qc-public-edge.service"
README_SRC="${SRC_ROOT}/README.md"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*"
}

require_root() {
  if [ "${EUID}" -ne 0 ]; then
    die "run with sudo/root"
  fi
}

backup_file() {
  local path="$1"
  local label="$2"
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  if [ -e "${path}" ]; then
    install -d -m 0750 "${BACKUP_DIR}"
    cp -a "${path}" "${BACKUP_DIR}/${label}.${stamp}.bak"
    info "backup: ${path} -> ${BACKUP_DIR}/${label}.${stamp}.bak"
  fi
}

require_root

[ -f "${CONFIG_SRC}" ] || die "config not found: ${CONFIG_SRC}"
[ -f "${UNIT_SRC}" ] || die "unit not found: ${UNIT_SRC}"
[ -f "${README_SRC}" ] || die "README not found: ${README_SRC}"
command -v haproxy >/dev/null 2>&1 || die "haproxy binary is missing; install the package first"
command -v systemctl >/dev/null 2>&1 || die "systemctl is missing"
command -v ss >/dev/null 2>&1 || die "ss is missing"

edge_listeners="$(ss -H -ltnp '( sport = :80 or sport = :443 )' 2>/dev/null || true)"
if [ -n "${edge_listeners}" ] && ! systemctl is-active --quiet "${SERVICE_NAME}"; then
  printf 'Review gate: port 80 or 443 already has a listener. This installer will not replace it.\n' >&2
  printf '%s\n' "${edge_listeners}" >&2
  exit 12
fi

if [ -e /etc/haproxy/haproxy.cfg ]; then
  info "notice: /etc/haproxy/haproxy.cfg exists and will not be modified"
fi
if systemctl is-active --quiet haproxy.service || systemctl is-enabled --quiet haproxy.service 2>/dev/null; then
  info "notice: haproxy.service exists and will not be modified"
fi

install -d -m 0755 "${DEST_DIR}"
install -d -m 0755 /run/qc-public-edge

haproxy -c -q -f "${CONFIG_SRC}"

backup_file "${CONFIG_DEST}" "haproxy.cfg"
backup_file "${README_DEST}" "README.public-edge.md"
backup_file "${UNIT_DEST}" "${SERVICE_NAME}"

install -m 0644 "${CONFIG_SRC}" "${CONFIG_DEST}"
install -m 0644 "${README_SRC}" "${README_DEST}"
install -m 0644 "${UNIT_SRC}" "${UNIT_DEST}"

haproxy -c -q -f "${CONFIG_DEST}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null

if systemctl is-active --quiet "${SERVICE_NAME}"; then
  systemctl reload-or-restart "${SERVICE_NAME}"
else
  systemctl start "${SERVICE_NAME}"
fi

systemctl --no-pager --lines=20 status "${SERVICE_NAME}"
