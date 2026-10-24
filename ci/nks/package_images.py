import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile
from common import ROOT, BUILD, SUFFIXES, read_snapshot, validate_snapshot, validate_release, write_json, main_guard

JAVA_ALIASES = {"auth": "auth-computer", "gateway": "gateway", "setupRegistry": "setup-registry",
                "jobOrchestrator": "job-orchestrator", "setupABe": "setup-a-be"}


def pin_base(image):
    subprocess.run(["docker", "pull", "--platform", "linux/amd64", image], check=True)
    digests = json.loads(subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"], text=True))
    if not digests or not re.search(r"@sha256:[0-9a-f]{64}$", digests[0]):
        raise RuntimeError("Base image digest could not be resolved")
    return digests[0]


def module_bundle(context, snapshot, version):
    contract = validate_snapshot(snapshot)
    source = ROOT / "workspace/setup-a"
    dist = source / "dist"
    paths = [path for path in dist.rglob("*") if path.is_file()
             and (path.relative_to(dist).as_posix() == "index.html" or path.relative_to(dist).parts[0] == "assets")
             and not any(part.startswith(".") for part in path.relative_to(dist).parts)]
    if not (dist / "index.html").is_file() or any(path.is_symlink() for path in paths):
        raise RuntimeError("Invalid NV dist artifact")
    artifact = context / "artifact"
    artifact.mkdir()
    archive = artifact / "module.zip"
    files = []
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for path in sorted(paths):
            relative = path.relative_to(dist).as_posix()
            content = path.read_bytes()
            target.writestr(relative, content)
            files.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
    raw = archive.read_bytes()
    manifest = {"sourceRepo": snapshot["repository"], "sourceSha": snapshot["callerSha"],
                "deploymentEnvironment": contract["name"], "sourceBranch": contract["branch"],
                "sourceFingerprint": snapshot["fingerprint"],
                "setupId": "stp_nv_a1", "version": version,
                "sha256": hashlib.sha256(raw).hexdigest(), "sizeBytes": len(raw), "files": files,
                "manifestMetadata": json.loads((source / "deployment/module-manifest.json").read_text()),
                "expectedModuleUrl": contract["moduleOrigin"] + "/module-assets/stp_nv_a1/module/" + version + "/index.html"}
    write_json(artifact / "manifest.json", manifest)
    shutil.copy2(Path(__file__).parent / "module_publisher.py", context / "module_publisher.py")
    shutil.copy2(Path(__file__).parent / "environments.py", context / "environments.py")


def main():
    snapshot = read_snapshot()
    contract = validate_snapshot(snapshot)
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    if not run_id.isdigit() or not attempt.isdigit():
        raise RuntimeError("Invalid workflow run identity")
    tag = contract["name"] + "-" + snapshot["callerSha"][:12] + "-" + run_id + "-" + attempt
    version = contract["moduleVersionPrefix"] + snapshot["fingerprint"][:12] + "-" + run_id + "-" + attempt
    if len(version) > 50:
        raise RuntimeError("Module version too long")
    release = {"deploymentEnvironment": contract["name"], "sourceRepository": snapshot["repository"], "sourceSha": snapshot["callerSha"],
               "sourceFingerprint": snapshot["fingerprint"], "images": []}
    for key in snapshot["outputs"]:
        context = BUILD / "contexts" / key
        context.mkdir(parents=True)
        repository = "ghcr.io/" + snapshot["repository"].lower() + "/" + SUFFIXES[key]
        if key in JAVA_ALIASES:
            alias = JAVA_ALIASES[key]
            jar_dir = ROOT / "workspace" / alias / ("build/libs" if alias == "gateway" else "api/build/libs")
            jars = [path for path in jar_dir.glob("*.jar") if not path.name.endswith(("-plain.jar", "-sources.jar", "-javadoc.jar"))]
            if len(jars) != 1:
                raise RuntimeError("Expected exactly one tested Boot JAR for " + alias)
            shutil.copy2(jars[0], context / "app.jar")
            base = pin_base("eclipse-temurin:25-jre")
            dockerfile = ('ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nWORKDIR /app\nCOPY app.jar /app/app.jar\n'
                          'ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=70.0 -XX:+ExitOnOutOfMemoryError"\n'
                          'USER 10001:10001\nENTRYPOINT ["java","-jar","/app/app.jar"]\n')
        elif key in ("shell", "setupModules"):
            base = pin_base("nginx:1.30.6")
            webroot = context / "html"
            if key == "shell":
                shutil.copytree(ROOT / "workspace/shell/dist", webroot)
            else:
                webroot.mkdir()
                shutil.copytree(ROOT / "workspace/sdk/dist", webroot / "qc-sdk")
            dockerfile = 'ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nCOPY html/ /usr/share/nginx/html/\nEXPOSE 8080\n'
        elif key == "modulePublisher":
            base = pin_base("container-registry.oracle.com/mysql/community-operator:9.6.0-2.2.7")
            module_bundle(context, snapshot, version)
            dockerfile = ('ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nCOPY artifact/ /artifact/\n'
                          'COPY module_publisher.py environments.py /publisher/\n'
                          'ENV HOME=/tmp MYSQLSH_USER_CONFIG_HOME=/tmp\n'
                          'ENTRYPOINT ["mysqlsh","--py","--file","/publisher/module_publisher.py"]\n')
        else:
            raise RuntimeError("Unexpected image component")
        (context / "Dockerfile").write_text(dockerfile)
        image = repository + ":" + tag
        subprocess.run(["docker", "build", "--platform", "linux/amd64", "--build-arg", "BASE_IMAGE=" + base,
                        "--label", "org.opencontainers.image.source=https://github.com/" + snapshot["repository"],
                        "--label", "org.opencontainers.image.revision=" + snapshot["callerSha"],
                        "--label", "org.opencontainers.image.base.digest=" + base.split("@", 1)[1],
                        "--tag", image, str(context)], check=True)
        release["images"].append({"key": key, "repository": repository, "tag": tag,
                                  "baseImage": base, **({"moduleVersion": version} if key == "modulePublisher" else {})})
    validate_release(snapshot, release)
    write_json(BUILD / "release.json", release)
    print("Prepared " + str(len(release["images"])) + " images; no registry push performed by this step")


if __name__ == "__main__":
    main_guard(main)
