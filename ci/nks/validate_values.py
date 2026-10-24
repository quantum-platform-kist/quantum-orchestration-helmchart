import json
from pathlib import Path
import re
import subprocess
import tempfile

KEYS = {"auth", "gateway", "setupRegistry", "jobOrchestrator", "setupABe", "shell", "setupModules", "modulePublisher"}


def main():
    directory = Path("values/nks-prod-images")
    if {path.stem for path in directory.glob("*.yaml")} != KEYS:
        raise RuntimeError("Unexpected or missing NKS component value files")
    files = sorted(directory.glob("*.yaml"))
    for path in files:
        data = json.loads(path.read_text())
        if path.stem == "modulePublisher":
            if set(data) != {"modulePublish"}:
                raise RuntimeError("Unexpected publisher values")
            publish = data["modulePublish"]
            if not publish["enabled"]:
                continue
            image = publish["image"]
        else:
            if set(data) != {"images"} or set(data["images"]) != {path.stem}:
                raise RuntimeError("Component file overrides an unrelated setting")
            image = data["images"][path.stem]
        if not {"repository", "tag", "digest"}.issubset(image) or set(image) - {"repository", "tag", "digest", "sourceFingerprint"} or not image["repository"].startswith("ghcr.io/quantum-platform-kist/") or not re.fullmatch(r"sha256:[0-9a-f]{64}", image["digest"]):
            raise RuntimeError("Invalid immutable image reference")
        fingerprint = data["modulePublish"].get("sourceFingerprint") if path.stem == "modulePublisher" else image.get("sourceFingerprint")
        if fingerprint is not None and not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise RuntimeError("Invalid source snapshot fingerprint")
    args = ["helm", "template", "qc-nks-prod-app", "charts/qc-prod-app", "--namespace", "quantum-cloud-prod",
            "-f", "values/prod-app.yaml", "-f", "values/prod-app-images.yaml", "-f", "values/nks-prod-app.yaml"]
    for path in files:
        args += ["-f", str(path)]
    args += ["-f", "values/prod-app-online.yaml", "-f", "ci/nks/render-site-values.yaml"]
    # Secret values never enter rendered values. Discard manifests; check rendering exit status.
    subprocess.run(args, stdout=subprocess.DEVNULL, check=True)
    # Exercise the optional PostSync publisher even before the first image is published.
    with tempfile.TemporaryDirectory(prefix="qc-nks-render-") as scratch:
        enabled = Path(scratch) / "publisher.json"
        enabled.write_text(json.dumps({"modulePublish": {"enabled": True, "version": "validation-only",
            "image": {"repository": "ghcr.io/quantum-platform-kist/quantum-setup-nv-frontend/module-publisher",
                      "tag": "validation-only", "digest": "sha256:" + "0" * 64}}}))
        subprocess.run(args + ["-f", str(enabled)], stdout=subprocess.DEVNULL, check=True)
    print("NKS component digests and optional publisher render validated")


if __name__ == "__main__":
    main()
