import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common
import checkout_sources
import module_publisher
import package_images
import writeback


class PipelineTests(unittest.TestCase):
    def test_public_helm_cannot_be_a_product_caller(self):
        with self.assertRaises(RuntimeError):
            common.identify(common.HELM)

    def test_component_values_are_narrow_and_digest_only(self):
        data = writeback.values({"key": "auth", "repository": "ghcr.io/quantum-platform-kist/quantum-orchestration-auth-computer/auth", "tag": "prod-test", "digest": "sha256:" + "a" * 64})
        self.assertEqual(set(data), {"images"})
        self.assertEqual(set(data["images"]), {"auth"})
        with self.assertRaises(RuntimeError):
            writeback.values({"key": "auth", "repository": "unused", "tag": "latest", "digest": "latest"})

    def test_duplicate_source_snapshot_only_skips_same_producer_and_fingerprint(self):
        repository = "quantum-platform-kist/quantum-orchestration-shell"
        value = {"images": {"shell": {"repository": "ghcr.io/" + repository + "/shell", "digest": "sha256:" + "a" * 64, "sourceFingerprint": "b" * 64}}}
        self.assertTrue(checkout_sources.deployed_snapshot_matches(value, "shell", repository, "b" * 64))
        self.assertFalse(checkout_sources.deployed_snapshot_matches(value, "shell", repository, "c" * 64))
        self.assertFalse(checkout_sources.deployed_snapshot_matches(value, "shell", "quantum-platform-kist/another-repo", "b" * 64))

    def test_gitops_ownership_rejected_before_api_calls(self):
        snapshot = {"repository": "quantum-platform-kist/quantum-orchestration-auth-computer", "callerSha": "a" * 40, "fingerprint": "f" * 64, "outputs": ["auth"]}
        release = {"sourceRepository": snapshot["repository"], "sourceSha": snapshot["callerSha"], "sourceFingerprint": snapshot["fingerprint"], "images": [
            {"key": "gateway", "repository": "ghcr.io/quantum-platform-kist/other/gateway", "tag": "prod", "digest": "sha256:" + "b" * 64}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "release.json").write_text(json.dumps(release))
            with mock.patch.object(writeback, "BUILD", path), mock.patch.object(writeback, "read_snapshot", return_value=snapshot), mock.patch.object(writeback, "token", return_value="fixture"), mock.patch.object(writeback, "github") as api:
                with self.assertRaises(RuntimeError):
                    writeback.main()
                api.assert_not_called()

    def test_gitops_update_uses_prod_and_retries_conflict(self):
        snapshot = {"repository": "quantum-platform-kist/quantum-orchestration-auth-computer", "callerSha": "a" * 40, "fingerprint": "f" * 64, "outputs": ["auth"]}
        release = {"sourceRepository": snapshot["repository"], "sourceSha": snapshot["callerSha"], "sourceFingerprint": snapshot["fingerprint"], "images": [
            {"key": "auth", "repository": "ghcr.io/" + snapshot["repository"] + "/auth", "tag": "prod", "digest": "sha256:" + "b" * 64}]}
        mutations = []
        def fake(method, path, credential, body=None):
            if method == "GET":
                return {"sha": "old-blob", "content": "e30="}
            mutations.append((path, copy.deepcopy(body)))
            if len(mutations) == 1:
                raise common.ApiError(409)
            return {"commit": {"sha": "new"}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "release.json").write_text(json.dumps(release))
            with mock.patch.object(writeback, "BUILD", path), mock.patch.object(writeback, "read_snapshot", return_value=snapshot), mock.patch.object(writeback, "token", return_value="fixture"), mock.patch.object(writeback, "github", side_effect=fake), mock.patch.object(writeback.time, "sleep"):
                writeback.main()
        self.assertEqual(len(mutations), 2)
        self.assertTrue(all(item[1]["branch"] == "prod" for item in mutations))
        self.assertTrue(all(item[0].endswith("/values/nks-prod-images/auth.yaml") for item in mutations))

    def test_nv_packer_matches_runtime_publisher_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "workspace/setup-a"
            (source / "dist/assets").mkdir(parents=True)
            (source / "deployment").mkdir()
            (source / "dist/index.html").write_text('<html><script src="./assets/index-test.js"></script></html>')
            (source / "dist/assets/index-test.js").write_text('console.log("fixture");')
            (source / "dist/assets/.hidden").write_text('must not ship')
            metadata = {"moduleNav": [{"key": "m_run", "label": "Run", "default": True}], "primaryAction": {"key": "open-registration", "viewKey": "m_run", "command": "open-registration"}}
            (source / "deployment/module-manifest.json").write_text(json.dumps(metadata))
            context = root / "context"
            context.mkdir()
            snapshot = {"repository": "quantum-platform-kist/quantum-setup-nv-frontend", "callerSha": "a" * 40}
            with mock.patch.object(package_images, "ROOT", root):
                package_images.module_bundle(context, snapshot, "nks-fixture-1")
            config, archive = module_publisher.load_bundle(context / "artifact")
            self.assertEqual(config["version"], "nks-fixture-1")
            self.assertEqual(len(config["files"]), 2)
            self.assertEqual(hashlib.sha256(archive).hexdigest(), config["archiveSha256"])


if __name__ == "__main__":
    unittest.main()
