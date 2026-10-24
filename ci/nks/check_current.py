from common import github, read_snapshot, token, output, main_guard


def main():
    snapshot = read_snapshot()
    credential = token("NKS_SOURCE_TOKEN")
    current = True
    for source in snapshot["sources"].values():
        tip = github("GET", "/repos/" + source["repository"] + "/git/ref/heads/prod", credential)["object"]["sha"]
        if tip != source["sha"]:
            current = False
    output("current", "true" if current else "false")
    print("Snapshot is current" if current else "Snapshot superseded; no image promotion, GitOps writeback or fan-out")


if __name__ == "__main__":
    main_guard(main)
