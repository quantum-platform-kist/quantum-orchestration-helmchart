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
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from environments import deployment_contract

spec = importlib.util.spec_from_file_location("module_publisher", Path(__file__).resolve().parents[1] / "module_publisher.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
VERSION = "nks-" + "b" * 12 + "-123-1"
FILES = {"index.html": b'<html><script type="module" src="./assets/app.js"></script></html>', "assets/app.js": b'console.log("offline module fixture");'}


def write_artifact(directory, files=None, environment="prod"):
    files = files or FILES
    contract = deployment_contract(environment)
    version = contract["moduleVersionPrefix"] + "b" * 12 + "-123-1"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    archive = output.getvalue()
    manifest = {"sourceRepo": "quantum-platform-kist/quantum-setup-nv-frontend", "sourceSha": "a" * 40,
                "version": version, "setupId": "stp_nv_a1",
                "deploymentEnvironment": environment, "sourceBranch": contract["branch"], "sourceFingerprint": "b" * 64, "sha256": hashlib.sha256(archive).hexdigest(), "sizeBytes": len(archive),
                "files": [{"path": name, "sha256": hashlib.sha256(body).hexdigest()} for name, body in files.items()],
                "manifestMetadata": {"moduleNav": [{"key": "m_run", "label": "Run", "default": True}], "primaryAction": {"key": "open-registration", "viewKey": "m_run", "command": "open-registration"}},
                "expectedModuleUrl": contract["moduleOrigin"] + "/module-assets/stp_nv_a1/module/" + version + "/index.html"}
    (directory / "module.zip").write_bytes(archive)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class FakeApi:
    def __init__(self, config, existing=False, active=False, trust=True, unknown_upload=False, concurrent=False):
        self.config, self.calls, self.reads = config, [], 0
        self.unknown_upload, self.concurrent = unknown_upload, concurrent
        self.version = self.version_response() if existing else None
        self.manifest = {"version": config["version"] if active else "0.1.0-nks-previous", "trustLevel": "FIRST_PARTY_TRUSTED", "tokenPolicy": "PLATFORM_JWT",
                         "directApiBaseUrl": config["expectedDirectApiBaseUrl"] if trust else "http://old.invalid",
                         "connectSrc": config["expectedConnectSrc"], "moduleNav": config["manifestMetadata"]["moduleNav"], "moduleUrl": config["expectedModuleUrl"]}

    def version_response(self):
        return {"setupId": "stp_nv_a1", "version": self.config["version"], "sha256": self.config["entrySha256"], "moduleUrl": self.config["expectedModuleUrl"], "sizeBytes": self.config["expandedBytes"]}

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
            self.manifest["version"] = self.config["version"]
            return 200, {}, b"", {}
        if modules:
            if token is not None:
                raise AssertionError("asset request must be anonymous")
            name = path.split(self.config["version"] + "/", 1)[1]
            return 200, None, FILES[name], {"Content-Security-Policy": "frame-ancestors " + deployment_contract()["origin"] + "; connect-src " + " ".join(self.config["expectedConnectSrc"])}
        raise AssertionError("unexpected internal route")


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.manifest = write_artifact(self.directory)
        self.config, _ = publisher.load_bundle(self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def run_case(self, environment="prod", **kwargs):
        api = FakeApi(self.config, **kwargs)
        output = io.StringIO()
        env = {"NKS_DEPLOYMENT_ENVIRONMENT": environment, "NKS_POD_NAMESPACE": "quantum-cloud-prod", "ARTIFACT_DIRECTORY": str(self.directory), "LOCAL_ADMIN_EMAIL": "admin@local", "LOCAL_ADMIN_PASSWORD": "PRIVATE_TEST_PASSWORD"}
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

    def test_dev_upload_activation_uses_dev_trust_and_existing_namespace(self):
        write_artifact(self.directory, environment="dev")
        with patch.dict(os.environ, {"NKS_DEPLOYMENT_ENVIRONMENT": "dev", "NKS_POD_NAMESPACE": "quantum-cloud-prod"}, clear=True):
            self.config, _ = publisher.load_bundle(self.directory)
        _, result, report = self.run_case(environment="dev")
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "published-and-verified")
        self.assertTrue(report["moduleUrl"].startswith("https://modules.qc-dev.quantum-instrument.com/"))

    def test_trust_contract_targets_shell_origin_setup_api(self):
        for environment in ("prod", "dev"):
            write_artifact(self.directory, environment=environment)
            with patch.dict(os.environ, {"NKS_DEPLOYMENT_ENVIRONMENT": environment, "NKS_POD_NAMESPACE": "quantum-cloud-prod"}, clear=True):
                config, _ = publisher.load_bundle(self.directory)
            origin = deployment_contract(environment)["origin"]
            self.assertEqual(config["expectedDirectApiBaseUrl"], origin + "/module/setup-a")
            self.assertEqual(config["expectedConnectSrc"], [origin, origin.replace("https://", "wss://", 1)])

    def test_artifact_environment_branch_origin_or_version_mismatch_stops_before_login(self):
        for runtime, artifact in (("dev", "prod"), ("prod", "dev")):
            for field in ("deploymentEnvironment", "sourceBranch", "expectedModuleUrl", "version", "sourceFingerprint"):
                manifest = write_artifact(self.directory, environment=runtime)
                if field == "deploymentEnvironment":
                    manifest[field] = artifact
                elif field == "sourceBranch":
                    manifest[field] = artifact
                elif field == "expectedModuleUrl":
                    manifest[field] = deployment_contract(artifact)["moduleOrigin"] + "/module-assets/stp_nv_a1/module/" + manifest["version"] + "/index.html"
                elif field == "version":
                    manifest[field] = deployment_contract(artifact)["moduleVersionPrefix"] + "b" * 12 + "-123-1"
                else:
                    manifest[field] = "c" * 64
                (self.directory / "manifest.json").write_text(json.dumps(manifest))
                env = {"NKS_DEPLOYMENT_ENVIRONMENT": runtime, "NKS_POD_NAMESPACE": "quantum-cloud-prod", "ARTIFACT_DIRECTORY": str(self.directory)}
                with self.subTest(runtime=runtime, field=field), patch.dict(os.environ, env, clear=True), patch.object(publisher, "call") as api, patch.object(publisher.OPENER, "open") as network, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(publisher.main(), 1)
                    api.assert_not_called()
                    network.assert_not_called()

    def test_namespace_mismatch_or_missing_dev_namespace_stops_before_login(self):
        for environment in ("prod", "dev"):
            write_artifact(self.directory, environment=environment)
            namespaces = ("quantum-cloud-dev", "other", "") if environment == "dev" else ("quantum-cloud-dev", "other")
            for namespace in namespaces:
                env = {"NKS_DEPLOYMENT_ENVIRONMENT": environment, "ARTIFACT_DIRECTORY": str(self.directory)}
                if namespace:
                    env["NKS_POD_NAMESPACE"] = namespace
                with self.subTest(environment=environment, namespace=namespace), patch.dict(os.environ, env, clear=True), patch.object(publisher, "call") as api, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(publisher.main(), 1)
                    api.assert_not_called()

    def test_http_requests_use_only_selected_internal_dns_host_and_origin(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.read.return_value = b"{}"
        response.__enter__.return_value.headers = {"Content-Type": "application/json"}
        for environment in ("prod", "dev"):
            contract = deployment_contract(environment)
            for modules in (False, True):
                with self.subTest(environment=environment, modules=modules), patch.dict(os.environ, {"NKS_DEPLOYMENT_ENVIRONMENT": environment, "NKS_POD_NAMESPACE": "quantum-cloud-prod"}, clear=True), patch.object(publisher.OPENER, "open", return_value=response) as network:
                    publisher.call("/fixture", method="POST", modules=modules)
                    req = network.call_args.args[0]
                    self.assertEqual(req.full_url, "http://" + ("setup-modules" if modules else "shell") + ".quantum-cloud-prod.svc.cluster.local:8080/fixture")
                    self.assertEqual(req.get_header("Host"), contract["moduleOrigin" if modules else "origin"].removeprefix("https://"))
                    self.assertEqual(req.get_header("Origin"), contract["origin"])

    def test_path_traversal_archive_rejected(self):
        write_artifact(self.directory, dict(FILES, **{"../escape.js": b"bad"}))
        with self.assertRaises(publisher.StopPublication):
            publisher.load_bundle(self.directory)


if __name__ == "__main__":
    unittest.main()
