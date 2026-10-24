import hashlib
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from common import ORG, HELM, ROOT, BUILD, TARGETS, ApiError, github, identify, token, output, write_json, main_guard


def deployed_snapshot_matches(values, key, repository, fingerprint):
    record = values["modulePublish"] if key == "modulePublisher" else values["images"][key]
    image = record["image"] if key == "modulePublisher" else record
    return (record.get("sourceFingerprint") == fingerprint
            and image["repository"].startswith("ghcr.io/" + repository.lower() + "/")
            and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("digest", ""))))


def main():
    if os.environ.get("GITHUB_REF") != "refs/heads/prod":
        raise RuntimeError("Only prod may build or deploy to NKS")
    repository = os.environ["GITHUB_REPOSITORY"]
    caller_sha = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", caller_sha):
        raise RuntimeError("Expected an immutable caller commit")
    alias, target = identify(repository)
    credential = token("NKS_SOURCE_TOKEN")
    metadata = github("GET", "/repos/" + repository, credential)
    if not metadata.get("private"):
        raise RuntimeError("Private product sources must be built in a private caller run")
    snapshot = {"repository": repository, "callerSha": caller_sha, "alias": alias,
                "runtime": target["runtime"], "sources": {}, "outputs": target["outputs"], "fanout": target["fanout"]}
    # The askpass file contains only an environment variable reference, never a token value.
    helper = Path(tempfile.mkdtemp(prefix="nks-git-")) / "askpass.sh"
    helper.write_text('#!/bin/sh\ncase "$1" in *sername*) printf "%s\\n" "x-access-token";; *) printf "%s\\n" "$NKS_SOURCE_TOKEN";; esac\n')
    helper.chmod(0o700)
    environment = dict(os.environ, GIT_ASKPASS=str(helper), GIT_TERMINAL_PROMPT="0")
    try:
        for dependency in target["needs"]:
            full_name = ORG + "/" + TARGETS[dependency]["repo"]
            sha = caller_sha if dependency == alias else github("GET", "/repos/" + full_name + "/git/ref/heads/prod", credential)["object"]["sha"]
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise RuntimeError("Invalid dependency commit")
            destination = ROOT / "workspace" / dependency
            destination.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "-c", "credential.helper=", "clone", "--filter=blob:none", "--no-checkout",
                            "https://github.com/" + full_name + ".git", str(destination)], env=environment, check=True)
            subprocess.run(["git", "-C", str(destination), "-c", "credential.helper=", "checkout", "--detach", sha], env=environment, check=True)
            actual = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
            if actual != sha:
                raise RuntimeError("Checkout commit mismatch")
            snapshot["sources"][dependency] = {"repository": full_name, "sha": sha}
    finally:
        helper.unlink(missing_ok=True)
        helper.parent.rmdir()
    tooling = Path(__file__).resolve().parent
    tooling_hash = hashlib.sha256()
    for path in sorted(tooling.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix in (".py", ".sh", ".json", ".gradle"):
            tooling_hash.update(path.relative_to(tooling).as_posix().encode())
            tooling_hash.update(path.read_bytes())
    tooling_hash.update((tooling.parent.parent / ".github/workflows/nks-reusable.yml").read_bytes())
    snapshot["toolingFingerprint"] = tooling_hash.hexdigest()
    snapshot["fingerprint"] = hashlib.sha256(json.dumps({"sources": snapshot["sources"], "tooling": snapshot["toolingFingerprint"]}, sort_keys=True).encode()).hexdigest()
    already_deployed = bool(target["outputs"])
    for key in target["outputs"]:
        try:
            current = github("GET", "/repos/" + HELM + "/contents/values/nks-prod-images/" + key + ".yaml?ref=prod", credential)
            values = json.loads(base64.b64decode(current["content"]))
            if not deployed_snapshot_matches(values, key, repository, snapshot["fingerprint"]):
                already_deployed = False
        except ApiError as error:
            if error.status != 404:
                raise
            already_deployed = False
        except (KeyError, ValueError):
            already_deployed = False
    write_json(BUILD / "source-snapshot.json", snapshot)
    output("runtime", target["runtime"])
    output("alias", alias)
    output("build_required", "false" if already_deployed else "true")
    print("Resolved " + str(len(snapshot["sources"])) + " private prod source commits; caller=" + repository)
    if already_deployed:
        print("Identical source/tooling snapshot is already deployed; skipping duplicate build and fan-out")


if __name__ == "__main__":
    main_guard(main)
