from common import github, read_snapshot, validate_snapshot, token, output, main_guard


def main():
    snapshot = read_snapshot()
    contract = validate_snapshot(snapshot)
    credential = token("NKS_SOURCE_TOKEN")
    current = True
    for source in snapshot["sources"].values():
        tip = github("GET", "/repos/" + source["repository"] + "/git/ref/heads/" + contract["branch"], credential)["object"]["sha"]
        if tip != source["sha"]:
            current = False
    output("current", "true" if current else "false")
    print("Snapshot is current" if current else "Snapshot superseded; no image promotion, GitOps writeback or fan-out")


if __name__ == "__main__":
    main_guard(main)
