"""Offline contract tests. No network, no real credentials, no Kubernetes writes."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("module_publisher", Path(__file__).resolve().parents[1] / "module_publisher.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
VERSION = "0.1.0-prod-0123456789ab"
FILES = {"index.html": b'<html><script type="module" src="./assets/app.js"></script></html>', "assets/app.js": b'console.log("offline module fixture");'}


def write_artifact(directory, files=None):
    files = files or FILES
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    archive = output.getvalue()
    manifest = {"sourceRepo": "quantum-platform-kist/quantum-setup-nv-frontend", "sourceSha": "a" * 40,
                "version": VERSION, "setupId": "stp_nv_a1", "sha256": hashlib.sha256(archive).hexdigest(), "sizeBytes": len(archive),
                "files": [{"path": name, "sha256": hashlib.sha256(body).hexdigest()} for name, body in files.items()],
                "manifestMetadata": {"moduleNav": [{"key": "m_run", "label": "Run", "default": True}], "primaryAction": {"key": "open-registration", "viewKey": "m_run", "command": "open-registration"}},
                "expectedModuleUrl": "https://modules.qc.quantum-instrument.com/module-assets/stp_nv_a1/module/" + VERSION + "/index.html"}
    (directory / "module.zip").write_bytes(archive)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class FakeApi:
    def __init__(self, config, existing=False, active=False, trust=True, unknown_upload=False, concurrent=False):
        self.config, self.calls, self.reads = config, [], 0
        self.unknown_upload, self.concurrent = unknown_upload, concurrent
        self.version = self.version_response() if existing else None
        self.manifest = {"version": VERSION if active else "0.1.0-nks-previous", "trustLevel": "FIRST_PARTY_TRUSTED", "tokenPolicy": "PLATFORM_JWT",
                         "directApiBaseUrl": config["expectedDirectApiBaseUrl"] if trust else "http://old.invalid",
                         "connectSrc": config["expectedConnectSrc"], "moduleNav": config["manifestMetadata"]["moduleNav"], "moduleUrl": config["expectedModuleUrl"]}

    def version_response(self):
        return {"setupId": "stp_nv_a1", "version": VERSION, "sha256": self.config["entrySha256"], "moduleUrl": self.config["expectedModuleUrl"], "sizeBytes": self.config["expandedBytes"]}

    def call(self, path, method="GET", token=None, payload=None, content_type=None, modules=False):
        self.calls.append((path, method, modules))
        if path.endswith("/auth/login"):
            return 200, {"accessToken": "PRIVATE_TEST_TOKEN"}, b"", {}
        if path.endswith("/auth/logout"):
            return 200, {}, b"", {}
        if path.endswith("/module-manifest"):
            self.reads += 1
            if self.concurrent and self.reads == 2:
                self.manifest["version"] = "0.1.0-concurrent-release"
            return 200, copy.deepcopy(self.manifest), b"", {}
        if path.endswith("/module-versions"):
            if method == "POST":
                if self.version is not None:
                    raise AssertionError("duplicate upload")
                self.version = self.version_response()
                return (0 if self.unknown_upload else 200), None, b"", {}
            return 200, [copy.deepcopy(self.version)] if self.version else [], b"", {}
        if path.endswith("/activate"):
            self.manifest["version"] = VERSION
            return 200, {}, b"", {}
        if modules:
            if token is not None:
                raise AssertionError("asset request must be anonymous")
            name = path.split(VERSION + "/", 1)[1]
            return 200, None, FILES[name], {"Content-Security-Policy": "frame-ancestors https://qc.quantum-instrument.com; connect-src " + " ".join(self.config["expectedConnectSrc"])}
        raise AssertionError("unexpected internal route")


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.manifest = write_artifact(self.directory)
        self.config, _ = publisher.load_bundle(self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def run_case(self, **kwargs):
        api = FakeApi(self.config, **kwargs)
        output = io.StringIO()
        env = {"ARTIFACT_DIRECTORY": str(self.directory), "LOCAL_ADMIN_EMAIL": "admin@local", "LOCAL_ADMIN_PASSWORD": "PRIVATE_TEST_PASSWORD"}
        with patch.dict(os.environ, env, clear=True), patch.object(publisher, "call", side_effect=api.call), contextlib.redirect_stdout(output):
            result = publisher.main()
        text = output.getvalue()
        self.assertNotIn("PRIVATE_TEST_PASSWORD", text)
        self.assertNotIn("PRIVATE_TEST_TOKEN", text)
        return api, result, json.loads(text.strip().split("QC_MODULE_PUBLISH:", 1)[1])

    def test_upload_activation_and_anonymous_assets(self):
        _, result, report = self.run_case()
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "published-and-verified")
        self.assertEqual(len(report["anonymousAssets"]), 2)

    def test_unknown_upload_response_is_inspected_not_retried(self):
        api, result, _ = self.run_case(unknown_upload=True)
        self.assertEqual(result, 0)
        self.assertEqual(sum(path.endswith("/module-versions") and method == "POST" for path, method, _ in api.calls), 1)

    def test_idempotent_existing_active_version(self):
        _, result, report = self.run_case(existing=True, active=True)
        self.assertEqual(result, 0)
        self.assertFalse(report["uploadAttempted"])
        self.assertFalse(report["activationAttempted"])

    def test_unreviewed_trust_blocks_upload_and_activation(self):
        _, result, report = self.run_case(trust=False)
        self.assertEqual(result, 1)
        self.assertFalse(report["uploadAttempted"])
        self.assertFalse(report["activationAttempted"])

    def test_concurrent_activation_is_not_overwritten(self):
        _, result, report = self.run_case(concurrent=True)
        self.assertEqual(result, 1)
        self.assertFalse(report["activationAttempted"])

    def test_wrong_archive_size_rejected(self):
        self.manifest["sizeBytes"] += 1
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest))
        with self.assertRaises(publisher.StopPublication):
            publisher.load_bundle(self.directory)

    def test_other_production_origin_rejected(self):
        self.manifest["expectedModuleUrl"] = "https://other.invalid/module"
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest))
        with self.assertRaises(publisher.StopPublication):
            publisher.load_bundle(self.directory)

    def test_trust_contract_targets_shell_origin_setup_api(self):
        self.assertEqual(self.config["expectedDirectApiBaseUrl"], "https://qc.quantum-instrument.com/module/setup-a")
        self.assertEqual(self.config["expectedConnectSrc"], ["https://qc.quantum-instrument.com", "wss://qc.quantum-instrument.com"])

    def test_module_origin_only_csp_is_rejected(self):
        api = FakeApi(self.config)
        original = api.call

        def legacy_csp(path, method="GET", token=None, payload=None, content_type=None, modules=False):
            status, body, raw, headers = original(path, method, token, payload, content_type, modules)
            if headers is not None:
                headers = {"Content-Security-Policy": "frame-ancestors https://qc.quantum-instrument.com; connect-src https://modules.qc.quantum-instrument.com wss://modules.qc.quantum-instrument.com"}
            return status, body, raw, headers
        api.call = legacy_csp
        output = io.StringIO()
        env = {"ARTIFACT_DIRECTORY": str(self.directory), "LOCAL_ADMIN_EMAIL": "admin@local", "LOCAL_ADMIN_PASSWORD": "PRIVATE_TEST_PASSWORD"}
        with patch.dict(os.environ, env, clear=True), patch.object(publisher, "call", side_effect=api.call), contextlib.redirect_stdout(output):
            self.assertEqual(publisher.main(), 1)
        self.assertIn("production_module_csp_verification_failed", output.getvalue())

    def test_path_traversal_archive_rejected(self):
        write_artifact(self.directory, dict(FILES, **{"../escape.js": b"bad"}))
        with self.assertRaises(publisher.StopPublication):
            publisher.load_bundle(self.directory)


if __name__ == "__main__":
    unittest.main()
