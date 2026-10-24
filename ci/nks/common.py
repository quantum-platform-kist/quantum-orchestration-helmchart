"""NKS CI helpers. These execute in the PRIVATE caller's Actions run."""
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

ORG = "quantum-platform-kist"
HELM = ORG + "/quantum-orchestration-helmchart"
ROOT = Path(os.environ.get("GITHUB_WORKSPACE", os.getcwd())).resolve()
BUILD = ROOT / "build"
TARGETS = json.loads((Path(__file__).parent / "targets.json").read_text())


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
        print("NKS CI failed: " + str(error))
        raise SystemExit(1) from None
