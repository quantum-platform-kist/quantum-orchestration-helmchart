import base64
import json
import re
import time
from common import HELM, BUILD, ApiError, github, read_snapshot, token, main_guard


def values(image, fingerprint=None):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image["digest"]):
        raise RuntimeError("Only immutable image digests may be promoted")
    value = {"repository": image["repository"], "tag": image["tag"], "digest": image["digest"]}
    if fingerprint is not None and not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise RuntimeError("Invalid source snapshot fingerprint")
    if image["key"] == "modulePublisher":
        return {"modulePublish": {"enabled": True, "image": value, "version": image["moduleVersion"], "sourceFingerprint": fingerprint}}
    if fingerprint is not None:
        value["sourceFingerprint"] = fingerprint
    return {"images": {image["key"]: value}}


def main():
    snapshot = read_snapshot()
    release = json.loads((BUILD / "release.json").read_text())
    if not release["images"]:
        return
    credential = token("NKS_GITOPS_TOKEN")
    if release["sourceRepository"] != snapshot["repository"] or release["sourceSha"] != snapshot["callerSha"] or release.get("sourceFingerprint") != snapshot.get("fingerprint"):
        raise RuntimeError("Release provenance differs from checked source")
    for image in release["images"]:
        if image["key"] not in snapshot["outputs"] or not image["repository"].startswith("ghcr.io/" + snapshot["repository"].lower() + "/"):
            raise RuntimeError("Component ownership mismatch")
        path = "/repos/" + HELM + "/contents/values/nks-prod-images/" + image["key"] + ".yaml"
        content = json.dumps(values(image, release.get("sourceFingerprint")), indent=2) + "\n"  # JSON is valid Helm YAML.
        for attempt in range(5):
            sha = None
            try:
                current = github("GET", path + "?ref=prod", credential)
                if base64.b64decode(current["content"]).decode() == content:
                    break
                sha = current["sha"]
            except ApiError as error:
                if error.status != 404:
                    raise
            payload = {"branch": "prod", "message": "deploy(nks): " + image["key"] + " " + snapshot["callerSha"][:12],
                       "content": base64.b64encode(content.encode()).decode()}
            if sha:
                payload["sha"] = sha
            try:
                github("PUT", path, credential, payload)
                print("Updated NKS prod digest: " + image["key"])
                break
            except ApiError as error:
                if error.status not in (409, 422) or attempt == 4:
                    raise
                time.sleep(2 ** attempt)


if __name__ == "__main__":
    main_guard(main)
