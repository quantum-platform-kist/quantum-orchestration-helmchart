from common import ORG, TARGETS, github, read_snapshot, validate_snapshot, token, main_guard


def main():
    snapshot = read_snapshot()
    contract = validate_snapshot(snapshot)
    if not snapshot["fanout"]:
        return
    credential = token("NKS_SOURCE_TOKEN")
    for alias in snapshot["fanout"]:
        repository = ORG + "/" + TARGETS[alias]["repo"]
        path = "/repos/" + repository + "/actions/workflows/" + contract["workflow"] + "/dispatches"
        # All callers must first be registered by their initial environment branch push.
        # A 404/403 fails clearly rather than dispatching a different branch.
        github("POST", path, credential, {"ref": contract["branch"]})
        print("Requested private " + contract["name"] + " consumer rebuild: " + repository)


if __name__ == "__main__":
    main_guard(main)
