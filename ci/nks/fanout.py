import time
from common import ORG, TARGETS, ApiError, github, read_snapshot, token, main_guard


def main():
    snapshot = read_snapshot()
    if not snapshot["fanout"]:
        return
    credential = token("NKS_SOURCE_TOKEN")
    for alias in snapshot["fanout"]:
        repository = ORG + "/" + TARGETS[alias]["repo"]
        path = "/repos/" + repository + "/actions/workflows/prod-nks.yml/dispatches"
        # All callers must first be registered by their initial prod push.
        # A 404/403 fails clearly rather than dispatching a different branch.
        github("POST", path, credential, {"ref": "prod"})
        print("Requested private prod consumer rebuild: " + repository)


if __name__ == "__main__":
    main_guard(main)
