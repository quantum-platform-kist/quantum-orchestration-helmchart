"""NKS-only module publication. No returned URLs are followed and no credential is logged."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, request
import uuid
import zipfile


SHELL = "http://shell.quantum-cloud-prod.svc.cluster.local:8080"
MODULES = "http://setup-modules.quantum-cloud-prod.svc.cluster.local:8080"
ORIGIN = "https://qc.quantum-instrument.com"
MODULE_ORIGIN = "https://modules.qc.quantum-instrument.com"
SETUP_ID = "stp_nv_a1"
VERSION = ""  # Set only from the verified local artifact manifest before API calls.
PREFIX = "/server/api/setups/" + SETUP_ID


class StopPublication(Exception):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = request.build_opener(request.ProxyHandler({}), NoRedirect())


def call(path, method="GET", token=None, payload=None, content_type=None, modules=False):
    if not path.startswith("/") or ".." in path:
        raise StopPublication("invalid_internal_path")
    headers = {"Host": "modules.qc.quantum-instrument.com" if modules else "qc.quantum-instrument.com", "Origin": ORIGIN, "X-Forwarded-Proto": "https", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if content_type:
        headers["Content-Type"] = content_type
    if isinstance(payload, dict):
        payload = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    req = request.Request((MODULES if modules else SHELL) + path, data=payload, headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=90) as response:
            raw = response.read(26 * 1024 * 1024)
            parsed = None
            if "json" in response.headers.get("Content-Type", ""):
                try:
                    parsed = json.loads(raw)
                except (ValueError, UnicodeError):
                    parsed = None
            return response.status, parsed, raw, response.headers
    except error.HTTPError as exc:
        return exc.code, None, b"", None
    except (error.URLError, OSError, TimeoutError):
        return 0, None, b"", None


def verify_version(item, config):
    return (isinstance(item, dict) and item.get("setupId") == SETUP_ID and item.get("version") == VERSION
            and item.get("sha256", "").lower() == config["entrySha256"]
            and item.get("moduleUrl") == config["expectedModuleUrl"]
            and item.get("sizeBytes") == config["expandedBytes"])


def get_version(token, config):
    status, body, _, _ = call(PREFIX + "/module-versions", token=token)
    if status != 200:
        raise StopPublication("version_inspection_failed")
    items = body.get("contents") if isinstance(body, dict) else body
    if not isinstance(items, list):
        raise StopPublication("unexpected_version_list")
    found = [item for item in items if item.get("version") == VERSION]
    if len(found) > 1 or (found and not verify_version(found[0], config)):
        raise StopPublication("existing_version_does_not_match_artifact")
    return found[0] if found else None


def get_manifest(token):
    status, body, _, _ = call(PREFIX + "/module-manifest", token=token)
    if status != 200 or not isinstance(body, dict):
        raise StopPublication("active_manifest_inspection_failed")
    return body


def verify_trust(manifest, config):
    return (manifest.get("trustLevel") == "FIRST_PARTY_TRUSTED" and manifest.get("tokenPolicy") == "PLATFORM_JWT"
            and manifest.get("directApiBaseUrl") == config["expectedDirectApiBaseUrl"]
            and set(manifest.get("connectSrc") or []) == set(config["expectedConnectSrc"]))


def multipart_body(config, archive):
    boundary = "qc-nks-" + uuid.uuid4().hex
    pieces = []
    for name, value, kind in (("version", VERSION, None), ("sha256", config["archiveSha256"], None), ("manifestMetadata", json.dumps(config["manifestMetadata"], ensure_ascii=False, separators=(",", ":")), "application/json")):
        header = "--" + boundary + '\r\nContent-Disposition: form-data; name="' + name + '"\r\n'
        if kind:
            header += "Content-Type: " + kind + "\r\n"
        pieces.append((header + "\r\n" + value + "\r\n").encode())
    pieces.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="setup-a-' + VERSION + '.zip"\r\nContent-Type: application/zip\r\n\r\n').encode() + archive + b"\r\n")
    pieces.append(("--" + boundary + "--\r\n").encode())
    return b"".join(pieces), "multipart/form-data; boundary=" + boundary



def load_bundle(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    archive = (directory / "module.zip").read_bytes()
    if not isinstance(manifest, dict) or manifest.get("sourceRepo") != "quantum-platform-kist/quantum-setup-nv-frontend":
        raise StopPublication("unexpected_artifact_repository")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("sourceSha", "")):
        raise StopPublication("invalid_pinned_source_sha")
    version = manifest.get("version", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,49}", version) or ".." in version or manifest.get("setupId") != SETUP_ID:
        raise StopPublication("invalid_fixed_setup_or_version")
    sha = manifest.get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha) or hashlib.sha256(archive).hexdigest() != sha:
        raise StopPublication("archive_checksum_mismatch")
    if type(manifest.get("sizeBytes")) is not int or len(archive) != manifest["sizeBytes"] or len(archive) > 25 * 1024 * 1024:
        raise StopPublication("archive_size_mismatch")
    expected_url = MODULE_ORIGIN + "/module-assets/" + SETUP_ID + "/module/" + version + "/index.html"
    if manifest.get("expectedModuleUrl") != expected_url:
        raise StopPublication("unexpected_production_module_url")
    metadata = manifest.get("manifestMetadata")
    if not isinstance(metadata, dict) or set(metadata) - {"moduleNav", "primaryAction"} or len(json.dumps(metadata).encode()) > 16 * 1024:
        raise StopPublication("invalid_ui_manifest_metadata")
    nav = metadata.get("moduleNav")
    if not isinstance(nav, list) or not 1 <= len(nav) <= 24:
        raise StopPublication("invalid_module_navigation")
    keys = []
    for item in nav:
        if not isinstance(item, dict) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", item.get("key", "")) or item["key"].lower() in {"queue", "members", "audit", "settings"}:
            raise StopPublication("invalid_module_navigation_key")
        if not isinstance(item.get("label"), str) or not 1 <= len(item["label"].strip()) <= 60:
            raise StopPublication("invalid_module_navigation_label")
        keys.append(item["key"])
    if len(set(keys)) != len(keys) or sum(item.get("default") is True for item in nav) > 1:
        raise StopPublication("ambiguous_module_navigation")
    action = metadata.get("primaryAction")
    if action is not None and (not isinstance(action, dict) or action.get("key") != "open-registration" or action.get("command") != "open-registration" or action.get("viewKey") not in keys):
        raise StopPublication("invalid_module_primary_action")
    declarations = manifest.get("files")
    if not isinstance(declarations, list) or not 1 <= len(declarations) <= 128:
        raise StopPublication("invalid_file_manifest")
    expected_files = {}
    for item in declarations:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")) or item["path"] in expected_files:
            raise StopPublication("invalid_or_duplicate_file_manifest")
        expected_files[item["path"]] = item["sha256"]
    files = []
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        entries = bundle.infolist()
        if len(entries) != len(expected_files) or {item.filename for item in entries} != set(expected_files):
            raise StopPublication("archive_file_set_mismatch")
        if sum(item.file_size for item in entries) > 25 * 1024 * 1024:
            raise StopPublication("expanded_archive_limit_exceeded")
        for item in entries:
            path = item.filename
            if item.is_dir() or len(path) > 240 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,180}", part) for part in path.split("/")):
                raise StopPublication("unsafe_archive_path")
            if ((item.external_attr >> 16) & 0o170000) not in {0, 0o100000} or item.file_size > 10 * 1024 * 1024:
                raise StopPublication("unsupported_archive_member")
            body = bundle.read(item)
            digest = hashlib.sha256(body).hexdigest()
            if digest != expected_files[path]:
                raise StopPublication("archive_member_checksum_mismatch")
            files.append({"path": path, "sha256": digest, "bytes": len(body)})
    if "index.html" not in expected_files or not any(item["path"].endswith((".js", ".mjs")) for item in files):
        raise StopPublication("missing_module_entry_or_javascript")
    config = {"version": version, "sourceRepo": manifest["sourceRepo"], "sourceSha": manifest["sourceSha"], "archiveSha256": sha,
              "archiveBytes": len(archive), "entrySha256": expected_files["index.html"], "expandedBytes": sum(item["bytes"] for item in files),
              "files": files, "manifestMetadata": metadata, "expectedModuleUrl": expected_url,
              "expectedDirectApiBaseUrl": MODULE_ORIGIN + "/api/setup-a",
              "expectedConnectSrc": [MODULE_ORIGIN, "wss://modules.qc.quantum-instrument.com"]}
    return config, archive


def main():
    global VERSION
    report = {"setupId": SETUP_ID, "status": "started", "uploadAttempted": False, "activationAttempted": False}
    token = None
    try:
        config, archive = load_bundle(Path(os.environ.get("ARTIFACT_DIRECTORY", "/artifact")))
        VERSION = config["version"]
        report.update({"version": VERSION, "sourceRepo": config["sourceRepo"], "sourceSha": config["sourceSha"], "archiveVerified": True})
        code, body, _, _ = call("/server/api/auth/login", "POST", payload={"email": os.environ["LOCAL_ADMIN_EMAIL"], "password": os.environ["LOCAL_ADMIN_PASSWORD"], "autoLogin": False})
        token = body.get("accessToken") if isinstance(body, dict) else None
        if code != 200 or not token:
            raise StopPublication("admin_login_failed")
        current = get_manifest(token)
        initial_version = current.get("version", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,49}", initial_version) or ".." in initial_version:
            raise StopPublication("invalid_current_module_version")
        if not verify_trust(current, config):
            raise StopPublication("reviewed_production_trust_configuration_not_ready")
        existing = get_version(token, config)
        if existing is None:
            multipart, content_type = multipart_body(config, archive)
            report["uploadAttempted"] = True
            status, _, _, _ = call(PREFIX + "/module-versions", "POST", token, multipart, content_type)
            report["uploadHttpStatus"] = status
            # A timeout is not permission to repeat the upload. Query immutable version first.
            existing = get_version(token, config)
            if existing is None:
                raise StopPublication("upload_outcome_unconfirmed_no_post_retry")
        report["versionVerified"] = True
        current = get_manifest(token)
        if not verify_trust(current, config) or current.get("version") not in {initial_version, VERSION}:
            raise StopPublication("active_configuration_changed_before_activation")
        if current.get("version") != VERSION:
            report["activationAttempted"] = True
            status, _, _, _ = call(PREFIX + "/module-versions/" + VERSION + "/activate", "POST", token)
            report["activationHttpStatus"] = status
        current = get_manifest(token)
        if current.get("version") != VERSION or current.get("moduleUrl") != config["expectedModuleUrl"] or not verify_trust(current, config):
            raise StopPublication("activation_outcome_unconfirmed_no_post_retry")
        if {item.get("key") for item in current.get("moduleNav", [])} != {item["key"] for item in config["manifestMetadata"]["moduleNav"]}:
            raise StopPublication("published_navigation_does_not_match")
        report.update({"activeManifestVerified": True, "moduleUrl": current["moduleUrl"], "directApiBaseUrl": current["directApiBaseUrl"]})
        assets = []
        for item in config["files"]:
            path = "/module-assets/" + SETUP_ID + "/module/" + VERSION + "/" + item["path"]
            code, _, raw, headers = call(path, modules=True)
            matches = code == 200 and len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"]
            assets.append({"httpStatus": code, "bytes": len(raw), "checksumMatches": matches})
            if not matches:
                raise StopPublication("anonymous_module_asset_verification_failed")
            if item["path"] == "index.html":
                csp = headers.get("Content-Security-Policy", "") if headers else ""
                if ORIGIN not in csp or MODULE_ORIGIN not in csp or "wss://modules.qc.quantum-instrument.com" not in csp:
                    raise StopPublication("production_module_csp_verification_failed")
        report.update({"anonymousAssets": assets, "status": "published-and-verified"})
    except Exception as exc:
        report.update({"status": "stopped", "safeReason": str(exc) if isinstance(exc, StopPublication) else "unexpected_error_details_suppressed"})
    finally:
        if token:
            code, _, _, _ = call("/server/api/auth/logout", "DELETE", token)
            report["logoutHttpStatus"] = code
        print("QC_MODULE_PUBLISH:" + json.dumps(report, separators=(",", ":")), flush=True)
    return 0 if report["status"] == "published-and-verified" else 1


if __name__ == "__main__":
    sys.exit(main())
