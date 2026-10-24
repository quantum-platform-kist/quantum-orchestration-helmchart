"""Cloud CI helpers. These execute in the PRIVATE caller's Actions run."""
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
from environments import deployment_contract

ORG = "quantum-platform-kist"
HELM = ORG + "/quantum-orchestration-helmchart"
ROOT = Path(os.environ.get("GITHUB_WORKSPACE", os.getcwd())).resolve()
BUILD = ROOT / "build"
TARGETS = json.loads((Path(__file__).parent / "targets.json").read_text())
SUFFIXES = {"auth": "auth", "gateway": "gateway", "setupRegistry": "setup-registry",
            "jobOrchestrator": "job-orchestrator", "setupABe": "setup-a-be",
            "shell": "shell", "setupModules": "setup-modules", "modulePublisher": "module-publisher"}


def ci_contract():
    contract = deployment_contract()
    if os.environ.get("GITHUB_REF") != "refs/heads/" + contract["branch"]:
        raise RuntimeError("Caller branch differs from deployment environment")
    return contract


def source_fingerprint(snapshot):
    return hashlib.sha256(json.dumps({"environment": snapshot["deploymentEnvironment"],
        "sources": snapshot["sources"], "tooling": snapshot["toolingFingerprint"]}, sort_keys=True).encode()).hexdigest()


def validate_snapshot(snapshot):
    contract = ci_contract()
    if snapshot.get("deploymentEnvironment") != contract["name"]:
        raise RuntimeError("Source snapshot differs from CI deployment environment")
    repository, caller_sha = snapshot.get("repository"), snapshot.get("callerSha", "")
    if repository != os.environ.get("GITHUB_REPOSITORY") or caller_sha != os.environ.get("GITHUB_SHA"):
        raise RuntimeError("Source snapshot differs from current caller")
    alias, target = identify(repository)
    if (not re.fullmatch(r"[0-9a-f]{40}", caller_sha) or snapshot.get("alias") != alias
            or snapshot.get("runtime") != target["runtime"] or snapshot.get("outputs") != target["outputs"]
            or snapshot.get("fanout") != target["fanout"] or set(snapshot.get("sources", {})) != set(target["needs"])):
        raise RuntimeError("Source snapshot differs from approved target contract")
    for name, source in snapshot["sources"].items():
        if (source.get("repository") != ORG + "/" + TARGETS[name]["repo"]
                or source.get("branch") != contract["branch"]
                or not re.fullmatch(r"[0-9a-f]{40}", source.get("sha", ""))):
            raise RuntimeError("Source dependency differs from deployment contract")
    if snapshot["sources"][alias]["sha"] != caller_sha:
        raise RuntimeError("Caller source differs from snapshot commit")
    if (not re.fullmatch(r"[0-9a-f]{64}", snapshot.get("toolingFingerprint", ""))
            or snapshot.get("fingerprint") != source_fingerprint(snapshot)):
        raise RuntimeError("Invalid environment-specific source fingerprint")
    return contract


def validate_release(snapshot, release, require_digest=False):
    contract = validate_snapshot(snapshot)
    if (release.get("deploymentEnvironment") != contract["name"]
            or release.get("sourceRepository") != snapshot["repository"]
            or release.get("sourceSha") != snapshot["callerSha"]
            or release.get("sourceFingerprint") != snapshot["fingerprint"]):
        raise RuntimeError("Release provenance differs from checked source and CI environment")
    images = release.get("images")
    if not isinstance(images, list) or sorted(image.get("key", "") for image in images) != sorted(snapshot["outputs"]):
        raise RuntimeError("Release components differ from approved producer outputs")
    for image in images:
        if image.get("repository") != "ghcr.io/" + snapshot["repository"].lower() + "/" + SUFFIXES[image["key"]]:
            raise RuntimeError("Component ownership mismatch")
        if not re.fullmatch(contract["name"] + "-" + snapshot["callerSha"][:12] + r"-[0-9]+-[0-9]+", image.get("tag", "")):
            raise RuntimeError("Image tag differs from source and deployment environment")
        if require_digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("digest", "")):
            raise RuntimeError("Only immutable image digests may be promoted")
        if image["key"] == "modulePublisher":
            prefix = contract["moduleVersionPrefix"]
            if not re.fullmatch(prefix + snapshot["fingerprint"][:12] + r"-[0-9]+-[0-9]+", image.get("moduleVersion", "")):
                raise RuntimeError("Module version differs from environment-specific source fingerprint")
    return contract


class ApiError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__("GitHub API HTTP " + str(status))


def token(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError("Required CI secret is empty: " + name)
    return value


def github(method, path, credential, body=None):
    if not path.startswith("/repos/" + ORG + "/"):
        raise RuntimeError("GitHub API outside approved organization repositories")
    if method != "GET":
        contract = ci_contract()
        if method == "PUT":
            permitted = {"/repos/" + HELM + "/contents/" + contract["valuesDirectory"] + "/" + key + ".yaml" for key in SUFFIXES}
            if path not in permitted or not isinstance(body, dict) or body.get("branch") != contract["branch"]:
                raise RuntimeError("GitOps mutation outside deployment environment")
        elif method == "POST":
            permitted = {"/repos/" + ORG + "/" + target["repo"] + "/actions/workflows/" + contract["workflow"] + "/dispatches" for target in TARGETS.values()}
            if path not in permitted or body != {"ref": contract["branch"]}:
                raise RuntimeError("Workflow dispatch outside deployment environment")
        else:
            raise RuntimeError("Unsupported CI GitHub mutation")
    headers = {"Authorization": "Bearer " + credential,
               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request("https://api.github.com" + path, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = response.read()
            return json.loads(result) if result else None
    except urllib.error.HTTPError as error:
        raise ApiError(error.code) from None


def identify(repository):
    for alias, target in TARGETS.items():
        if repository == ORG + "/" + target["repo"]:
            return alias, target
    raise RuntimeError("Caller is not one of the 11 approved private source repositories")


def read_snapshot():
    return json.loads((BUILD / "source-snapshot.json").read_text())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def output(name, value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or "\n" in str(value):
        raise RuntimeError("Invalid workflow output")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(name + "=" + str(value) + "\n")


def main_guard(fn):
    try:
        fn()
    except Exception as error:
        # No HTTP bodies or credentials are included in controlled error messages.
        print("Cloud CI failed: " + str(error))
        raise SystemExit(1) from None
