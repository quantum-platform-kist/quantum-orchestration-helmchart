"""Validate immutable image overrides for NKS prod and existing on-prem dev."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
from common import ORG, SUFFIXES, TARGETS
from environments import deployment_contract

KEYS = set(SUFFIXES)
PRODUCERS = {key: "ghcr.io/" + ORG + "/" + target["repo"] + "/" + SUFFIXES[key]
             for target in TARGETS.values() for key in target["outputs"]}


def validate_directory(directory, environment):
    contract = deployment_contract(environment)
    if {path.stem for path in directory.glob("*.yaml")} != KEYS:
        raise RuntimeError("Unexpected or missing " + environment + " component value files")
    files = sorted(directory.glob("*.yaml"))
    for path in files:
        data = json.loads(path.read_text())
        publisher = path.stem == "modulePublisher"
        if publisher:
            if set(data) != {"modulePublish"}:
                raise RuntimeError("Unexpected publisher values")
            record = data["modulePublish"]
            if set(record) - {"enabled", "image", "version", "sourceFingerprint"} or type(record.get("enabled")) is not bool:
                raise RuntimeError("Publisher file overrides an unrelated setting")
            image = record["image"]
        else:
            if set(data) != {"images"} or set(data["images"]) != {path.stem}:
                raise RuntimeError("Component file overrides an unrelated setting")
            record = image = data["images"][path.stem]
        if (not {"repository", "tag", "digest"}.issubset(image)
                or set(image) - {"repository", "tag", "digest", "sourceFingerprint"}
                or image["repository"] != PRODUCERS[path.stem]
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", image["digest"])):
            raise RuntimeError("Invalid immutable image reference")
        fingerprint = record.get("sourceFingerprint")
        if fingerprint is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise RuntimeError("Invalid source snapshot fingerprint")
            if not re.fullmatch(environment + r"-[0-9a-f]{12}-[0-9]+-[0-9]+", image["tag"]):
                raise RuntimeError("Published image tag differs from deployment environment")
        if publisher and record["enabled"]:
            if environment == "dev" and fingerprint is None:
                raise RuntimeError("Dev publisher requires a newly built dev artifact")
            if not re.fullmatch(contract["moduleVersionPrefix"] + (fingerprint or "")[:12] + r"-[0-9]+-[0-9]+", record.get("version", "")):
                raise RuntimeError("Publisher version differs from deployment environment")
    return files


def main():
    for environment in ("prod", "dev"):
        contract = deployment_contract(environment)
        files = validate_directory(Path(contract["valuesDirectory"]), environment)
        args = ["helm", "template", "qc-" + environment + "-app-validation", "charts/qc-prod-app", "--namespace", contract["namespace"],
                "-f", "values/prod-app.yaml", "-f", "values/prod-app-images.yaml"]
        if environment == "prod":
            args += ["-f", "values/nks-prod-app.yaml"]
        args += ["-f", "values/prod-app-online.yaml"]
        if environment == "dev":
            args += ["-f", "values/dev-dns.yaml", "--set-string", "global.deploymentEnvironment=dev"]
        for path in files:
            args += ["-f", str(path)]
        if environment == "prod":
            args += ["-f", "ci/nks/render-site-values.yaml"]
        # Secret values never enter rendered values. No cluster access is used.
        subprocess.run(args, stdout=subprocess.DEVNULL, check=True)
        # Exercise the optional hook even while Argo keeps it paused.
        with tempfile.TemporaryDirectory(prefix="qc-cloud-render-") as scratch:
            enabled = Path(scratch) / "publisher.json"
            enabled.write_text(json.dumps({"modulePublish": {"enabled": True, "version": "validation-only",
                "image": {"repository": PRODUCERS["modulePublisher"],
                          "tag": "validation-only", "digest": "sha256:" + "0" * 64}}}))
            subprocess.run(args + ["-f", str(enabled)], stdout=subprocess.DEVNULL, check=True)
    print("NKS prod and on-prem dev component digests and optional publisher renders validated")


if __name__ == "__main__":
    main()
