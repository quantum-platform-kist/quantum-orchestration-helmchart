"""Offline CI isolation tests: every network or registry operation is mocked."""
import base64
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
import check_current
import common
import checkout_sources
from environments import deployment_contract
import fanout
import module_publisher
import package_images
import push_images
import validate_values
import writeback


def snapshot_for(environment="prod", alias="auth-computer"):
    target = common.TARGETS[alias]
    snapshot = {"deploymentEnvironment": environment, "repository": common.ORG + "/" + target["repo"],
                "callerSha": "a" * 40, "alias": alias, "runtime": target["runtime"],
                "outputs": target["outputs"], "fanout": target["fanout"], "toolingFingerprint": "b" * 64,
                "sources": {name: {"repository": common.ORG + "/" + common.TARGETS[name]["repo"],
                                     "sha": "a" * 40, "branch": environment} for name in target["needs"]}}
    snapshot["fingerprint"] = common.source_fingerprint(snapshot)
    return snapshot


def ci_env(snapshot):
    return {"NKS_DEPLOYMENT_ENVIRONMENT": snapshot["deploymentEnvironment"],
            "GITHUB_REF": "refs/heads/" + snapshot["deploymentEnvironment"],
            "GITHUB_REPOSITORY": snapshot["repository"], "GITHUB_SHA": snapshot["callerSha"],
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}


def release_for(snapshot):
    contract = deployment_contract(snapshot["deploymentEnvironment"])
    return {"deploymentEnvironment": contract["name"], "sourceRepository": snapshot["repository"],
            "sourceSha": snapshot["callerSha"], "sourceFingerprint": snapshot["fingerprint"],
            "images": [{"key": key, "repository": "ghcr.io/" + snapshot["repository"] + "/" + common.SUFFIXES[key],
                        "tag": contract["name"] + "-" + snapshot["callerSha"][:12] + "-123-1", "digest": "sha256:" + "b" * 64,
                        **({"moduleVersion": contract["moduleVersionPrefix"] + snapshot["fingerprint"][:12] + "-123-1"} if key == "modulePublisher" else {})}
                       for key in snapshot["outputs"]]}


class PipelineTests(unittest.TestCase):
    def test_auth_client_changes_rebuild_job_from_the_same_auth_source(self):
        self.assertIn("auth", common.TARGETS["job-orchestrator"]["needs"])
        self.assertIn("job-orchestrator", common.TARGETS["auth"]["fanout"])

    def test_public_helm_cannot_be_a_product_caller(self):
        with self.assertRaises(RuntimeError):
            common.identify(common.HELM)

    def test_environment_contract_is_closed_and_existing_namespace_is_retained(self):
        for invalid in ("staging", "dev/../prod", "", None, 3):
            with self.subTest(invalid=invalid), mock.patch.dict(os.environ, {"NKS_DEPLOYMENT_ENVIRONMENT": "bad"}, clear=True):
                with self.assertRaises(RuntimeError):
                    deployment_contract(invalid)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(deployment_contract()["name"], "prod")
        self.assertEqual(deployment_contract("dev")["namespace"], "quantum-cloud-prod")
        self.assertEqual(deployment_contract("dev")["appSecret"], "qc-prod-app-secrets")
        self.assertEqual(deployment_contract("dev")["valuesDirectory"], "values/onprem-dev-images")

    def test_component_values_are_narrow_and_digest_only(self):
        data = writeback.values(release_for(snapshot_for())["images"][0])
        self.assertEqual(set(data), {"images"})
        self.assertEqual(set(data["images"]), {"auth"})
        with self.assertRaises(RuntimeError):
            writeback.values({"key": "auth", "repository": "unused", "tag": "latest", "digest": "latest"})

    def test_same_shas_have_environment_specific_fingerprints(self):
        prod, dev = snapshot_for("prod"), snapshot_for("dev")
        self.assertNotEqual(prod["fingerprint"], dev["fingerprint"])
        value = writeback.values(release_for(prod)["images"][0], prod["fingerprint"])
        self.assertTrue(checkout_sources.deployed_snapshot_matches(value, "auth", prod["repository"], prod["fingerprint"]))
        self.assertFalse(checkout_sources.deployed_snapshot_matches(value, "auth", dev["repository"], dev["fingerprint"]))
        self.assertFalse(checkout_sources.deployed_snapshot_matches(value, "auth", common.ORG + "/another", prod["fingerprint"]))

    def test_all_checkout_dependencies_and_dedup_reads_follow_selected_branch(self):
        for environment in ("prod", "dev"):
            for alias in common.TARGETS:
                snapshot = snapshot_for(environment, alias)
                calls = []
                def fake(method, path, credential, body=None):
                    calls.append((method, path))
                    if path == "/repos/" + snapshot["repository"]:
                        return {"private": True}
                    if "/git/ref/heads/" in path:
                        return {"object": {"sha": "a" * 40}}
                    raise common.ApiError(404)
                with self.subTest(environment=environment, alias=alias), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    with mock.patch.dict(os.environ, ci_env(snapshot), clear=True), mock.patch.object(checkout_sources, "ROOT", root), mock.patch.object(checkout_sources, "BUILD", root / "build"), mock.patch.object(checkout_sources, "token", return_value="fixture"), mock.patch.object(checkout_sources, "github", side_effect=fake), mock.patch.object(checkout_sources, "output"), mock.patch.object(checkout_sources.subprocess, "run"), mock.patch.object(checkout_sources.subprocess, "check_output", return_value="a" * 40):
                        checkout_sources.main()
                    actual = json.loads((root / "build/source-snapshot.json").read_text())
                    self.assertEqual(actual["deploymentEnvironment"], environment)
                    self.assertTrue(all(s["branch"] == environment for s in actual["sources"].values()))
                    self.assertTrue(all(path.endswith("/" + environment) for _, path in calls if "/git/ref/heads/" in path))
                    self.assertTrue(all("/" + deployment_contract(environment)["valuesDirectory"] + "/" in path and path.endswith("?ref=" + environment) for _, path in calls if "/contents/" in path))

    def test_current_checks_selected_branch_and_reports_stale_sources(self):
        for environment in ("dev", "prod"):
            snapshot = snapshot_for(environment, "job-orchestrator")
            with self.subTest(environment=environment), mock.patch.dict(os.environ, ci_env(snapshot), clear=True), mock.patch.object(check_current, "read_snapshot", return_value=snapshot), mock.patch.object(check_current, "token", return_value="fixture"), mock.patch.object(check_current, "github", return_value={"object": {"sha": "f" * 40}}) as api, mock.patch.object(check_current, "output") as output:
                check_current.main()
                output.assert_called_once_with("current", "false")
                self.assertTrue(all(call.args[1].endswith("/heads/" + environment) for call in api.call_args_list))

    def test_gitops_update_uses_selected_directory_and_retries_conflict(self):
        for environment in ("prod", "dev"):
            snapshot = snapshot_for(environment)
            release = release_for(snapshot)
            mutations, reads = [], []
            def fake(method, path, credential, body=None):
                if method == "GET":
                    reads.append(path)
                    return {"sha": "old-blob", "content": "e30="}
                mutations.append((path, copy.deepcopy(body)))
                if len(mutations) == 1:
                    raise common.ApiError(409)
                return {"commit": {"sha": "new"}}
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as folder:
                path = Path(folder)
                (path / "release.json").write_text(json.dumps(release))
                with mock.patch.dict(os.environ, ci_env(snapshot), clear=True), mock.patch.object(writeback, "BUILD", path), mock.patch.object(writeback, "read_snapshot", return_value=snapshot), mock.patch.object(writeback, "token", return_value="fixture"), mock.patch.object(writeback, "github", side_effect=fake), mock.patch.object(writeback.time, "sleep"):
                    writeback.main()
            self.assertEqual(len(mutations), 2)
            self.assertTrue(all(item[1]["branch"] == environment for item in mutations))
            self.assertTrue(all(item[0].endswith("/" + deployment_contract(environment)["valuesDirectory"] + "/auth.yaml") for item in mutations))
            self.assertTrue(all(path.endswith("?ref=" + environment) for path in reads))
            result = json.loads(base64.b64decode(mutations[-1][1]["content"]))
            self.assertEqual(result["images"]["auth"]["sourceFingerprint"], snapshot["fingerprint"])

    def test_cross_environment_releases_rejected_before_any_push_or_gitops_api(self):
        for environment, other in (("dev", "prod"), ("prod", "dev")):
            snapshot = snapshot_for(environment)
            base = release_for(snapshot)
            variants = []
            for key, value in (("deploymentEnvironment", other), ("sourceFingerprint", "0" * 64)):
                release = copy.deepcopy(base)
                release[key] = value
                variants.append(release)
            for key, value in (("tag", other + "-aaaaaaaaaaaa-123-1"), ("key", "gateway"), ("repository", "ghcr.io/other/repo/auth")):
                release = copy.deepcopy(base)
                release["images"][0][key] = value
                variants.append(release)
            for release in variants:
                with self.subTest(environment=environment, release=release), tempfile.TemporaryDirectory() as folder:
                    path = Path(folder)
                    (path / "release.json").write_text(json.dumps(release))
                    for module in (writeback, push_images):
                        with mock.patch.dict(os.environ, ci_env(snapshot), clear=True), mock.patch.object(module, "BUILD", path), mock.patch.object(module, "read_snapshot", return_value=snapshot), mock.patch.object(module, "token") as token, mock.patch.object(common.urllib.request, "urlopen") as network, mock.patch.object(push_images.subprocess, "run") as docker:
                            with self.assertRaises(RuntimeError):
                                module.main()
                            token.assert_not_called()
                            network.assert_not_called()
                            docker.assert_not_called()

    def test_mixed_source_snapshot_rejected_before_current_or_fanout_api(self):
        for environment, other in (("dev", "prod"), ("prod", "dev")):
            snapshot = snapshot_for(environment, "core")
            env = ci_env(snapshot)
            for corruption in ("environment", "branch", "fingerprint", "caller"):
                changed = copy.deepcopy(snapshot)
                if corruption == "environment":
                    changed["deploymentEnvironment"] = other
                elif corruption == "branch":
                    changed["sources"]["core"]["branch"] = other
                    changed["fingerprint"] = common.source_fingerprint(changed)
                elif corruption == "fingerprint":
                    changed["fingerprint"] = "0" * 64
                else:
                    env = dict(ci_env(snapshot), GITHUB_REF="refs/heads/" + other)
                for module in (fanout, check_current):
                    with self.subTest(environment=environment, corruption=corruption, module=module.__name__), mock.patch.dict(os.environ, env, clear=True), mock.patch.object(module, "read_snapshot", return_value=changed), mock.patch.object(module, "token") as token, mock.patch.object(module, "github") as api:
                        with self.assertRaises(RuntimeError):
                            module.main()
                        api.assert_not_called()
                        token.assert_not_called()

    def test_fanout_uses_environment_workflow_and_ref(self):
        for environment in ("dev", "prod"):
            snapshot = snapshot_for(environment, "core")
            with self.subTest(environment=environment), mock.patch.dict(os.environ, ci_env(snapshot), clear=True), mock.patch.object(fanout, "read_snapshot", return_value=snapshot), mock.patch.object(fanout, "token", return_value="fixture"), mock.patch.object(fanout, "github") as api:
                fanout.main()
                self.assertEqual(api.call_count, len(snapshot["fanout"]))
                self.assertTrue(all(call.args[1].endswith("/workflows/" + environment + "-nks.yml/dispatches") and call.args[3] == {"ref": environment} for call in api.call_args_list))

    def test_api_guard_blocks_wrong_branch_directory_and_dispatch_before_network(self):
        for environment, other in (("dev", "prod"), ("prod", "dev")):
            snapshot = snapshot_for(environment)
            contract, opposite = deployment_contract(environment), deployment_contract(other)
            allowed_path = "/repos/" + common.HELM + "/contents/" + contract["valuesDirectory"] + "/auth.yaml"
            opposite_path = "/repos/" + common.HELM + "/contents/" + opposite["valuesDirectory"] + "/auth.yaml"
            workflow_path = "/repos/" + snapshot["repository"] + "/actions/workflows/"
            operations = [("PUT", allowed_path, {"branch": other}), ("PUT", opposite_path, {"branch": environment}),
                          ("POST", workflow_path + contract["workflow"] + "/dispatches", {"ref": other}),
                          ("POST", workflow_path + opposite["workflow"] + "/dispatches", {"ref": environment})]
            with mock.patch.dict(os.environ, ci_env(snapshot), clear=True), mock.patch.object(common.urllib.request, "urlopen") as network:
                for method, path, body in operations:
                    with self.subTest(environment=environment, path=path, body=body), self.assertRaises(RuntimeError):
                        common.github(method, path, "fixture", body)
                network.assert_not_called()

    def test_packer_validates_snapshot_before_docker_or_build_outputs(self):
        snapshot = snapshot_for("dev")
        with mock.patch.dict(os.environ, ci_env(snapshot_for("prod")), clear=True), mock.patch.object(package_images, "read_snapshot", return_value=snapshot), mock.patch.object(package_images.subprocess, "run") as docker:
            with self.assertRaises(RuntimeError):
                package_images.main()
            docker.assert_not_called()

    def test_empty_producer_release_retains_environment_provenance(self):
        for environment in ("dev", "prod"):
            snapshot = snapshot_for(environment, "core")
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as folder:
                with mock.patch.dict(os.environ, ci_env(snapshot), clear=True), mock.patch.object(package_images, "BUILD", Path(folder)), mock.patch.object(package_images, "read_snapshot", return_value=snapshot), mock.patch.object(package_images.subprocess, "run") as docker:
                    package_images.main()
                    docker.assert_not_called()
                release = json.loads((Path(folder) / "release.json").read_text())
                self.assertEqual(release["deploymentEnvironment"], environment)
                self.assertEqual(release["sourceFingerprint"], snapshot["fingerprint"])
                self.assertEqual(release["images"], [])

    def test_packaged_image_tag_and_release_follow_selected_environment(self):
        for environment in ("dev", "prod"):
            snapshot = snapshot_for(environment, "shell")
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                dist = root / "workspace/shell/dist"
                dist.mkdir(parents=True)
                (dist / "index.html").write_text("fixture")
                with mock.patch.dict(os.environ, ci_env(snapshot), clear=True), mock.patch.object(package_images, "ROOT", root), mock.patch.object(package_images, "BUILD", root / "build"), mock.patch.object(package_images, "read_snapshot", return_value=snapshot), mock.patch.object(package_images, "pin_base", return_value="nginx@sha256:" + "c" * 64), mock.patch.object(package_images.subprocess, "run") as docker:
                    package_images.main()
                release = json.loads((root / "build/release.json").read_text())
                self.assertEqual(release["deploymentEnvironment"], environment)
                self.assertEqual(release["images"][0]["tag"], environment + "-aaaaaaaaaaaa-123-1")
                self.assertIn("ghcr.io/" + snapshot["repository"] + "/shell:" + release["images"][0]["tag"], docker.call_args.args[0])

    def test_dev_image_values_reject_prod_fingerprint_and_unreviewed_publisher_seed(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for alias in common.TARGETS:
                snapshot = snapshot_for("dev", alias)
                for image in release_for(snapshot)["images"]:
                    (directory / (image["key"] + ".yaml")).write_text(json.dumps(writeback.values(image, snapshot["fingerprint"])))
            validate_values.validate_directory(directory, "dev")
            path = directory / "auth.yaml"
            data = json.loads(path.read_text())
            original_tag = data["images"]["auth"]["tag"]
            data["images"]["auth"]["tag"] = original_tag.replace("dev-", "prod-", 1)
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(RuntimeError, "tag differs"):
                validate_values.validate_directory(directory, "dev")
            data["images"]["auth"]["tag"] = original_tag
            path.write_text(json.dumps(data))
            path = directory / "modulePublisher.yaml"
            data = json.loads(path.read_text())
            data["modulePublish"].pop("sourceFingerprint")
            data["modulePublish"]["enabled"] = True
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(RuntimeError, "newly built dev artifact"):
                validate_values.validate_directory(directory, "dev")

    def test_nv_packer_matches_each_runtime_publisher_contract(self):
        for environment in ("prod", "dev"):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as folder:
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
                snapshot = snapshot_for(environment, "setup-a")
                version = release_for(snapshot)["images"][0]["moduleVersion"]
                env = dict(ci_env(snapshot), NKS_POD_NAMESPACE="quantum-cloud-prod")
                with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(package_images, "ROOT", root):
                    package_images.module_bundle(context, snapshot, version)
                    config, archive = module_publisher.load_bundle(context / "artifact")
                self.assertEqual(config["version"], version)
                self.assertTrue(config["expectedModuleUrl"].startswith(deployment_contract(environment)["moduleOrigin"]))
                self.assertEqual(len(config["files"]), 2)
                self.assertEqual(hashlib.sha256(archive).hexdigest(), config["archiveSha256"])
                self.assertTrue((context / "environments.py").exists())


if __name__ == "__main__":
    unittest.main()
