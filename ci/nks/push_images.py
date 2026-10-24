import json
import os
import re
import subprocess
from common import BUILD, read_snapshot, validate_release, token, write_json, main_guard


def main():
    release = json.loads((BUILD / "release.json").read_text())
    snapshot = read_snapshot()
    validate_release(snapshot, release)
    if not release["images"]:
        return
    credential = token("GHCR_TOKEN")
    subprocess.run(["docker", "login", "ghcr.io", "--username", os.environ["GHCR_USERNAME"], "--password-stdin"],
                   input=credential + "\n", text=True, check=True)
    try:
        for image in release["images"]:
            prefix = "ghcr.io/" + snapshot["repository"].lower() + "/"
            if not image["repository"].startswith(prefix):
                raise RuntimeError("Refusing to publish another repository's package")
            reference = image["repository"] + ":" + image["tag"]
            subprocess.run(["docker", "push", reference], check=True)
            digests = json.loads(subprocess.check_output(["docker", "image", "inspect", reference, "--format", "{{json .RepoDigests}}"], text=True))
            matches = [item.split("@", 1)[1] for item in digests if item.startswith(image["repository"] + "@")]
            if len(matches) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", matches[0]):
                raise RuntimeError("Pushed image digest is ambiguous")
            image["digest"] = matches[0]
    finally:
        subprocess.run(["docker", "logout", "ghcr.io"], check=False)
    write_json(BUILD / "release.json", release)


if __name__ == "__main__":
    main_guard(main)
