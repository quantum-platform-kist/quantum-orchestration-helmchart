#!/usr/bin/env python3
"""실제 Argo values로 온프레미스 dev / NKS prod 격리를 검증합니다. 외부 API는 호출하지 않습니다."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile

import yaml


ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = {"auth", "gateway", "setupRegistry", "jobOrchestrator", "setupABe",
              "shell", "setupModules", "modulePublisher"}
PUBLISHER_NAME = "qc-nv-module-publish"
PUBLISHER_REPOSITORY = "ghcr.io/quantum-platform-kist/quantum-setup-nv-frontend/module-publisher"
SPEC = importlib.util.spec_from_file_location("nks_render", ROOT / "scripts/render-check-nks.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def read(path):
    return yaml.safe_load(path.read_text())


def render(application, parameter_overrides=None):
    source = application["spec"]["source"]
    config = source["helm"]
    chart = ROOT / source["path"]
    args = ["helm", "template", application["metadata"]["name"], str(chart),
            "--namespace", application["spec"]["destination"]["namespace"]]
    for filename in config["valueFiles"]:
        path = (chart / filename).resolve()
        require(path.is_relative_to(ROOT / "values"), "Argo values must stay within values/")
        args += ["-f", str(path)]
    with tempfile.TemporaryDirectory(prefix="qc-argo-render-") as scratch:
        values = Path(scratch) / "inline.yaml"
        values.write_text(yaml.safe_dump(config.get("valuesObject", {})))
        args += ["-f", str(values)]
        for item in config.get("parameters", []):
            args += ["--set-string" if item.get("forceString") else "--set",
                     item["name"] + "=" + item["value"]]
        for name, value in (parameter_overrides or {}).items():
            args += ["--set", name + "=" + value]
        output = subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True)
    return list(filter(None, yaml.safe_load_all(output.stdout)))


def check_application(environment, role):
    name = f"qc-nks-prod-{role}" if environment == "prod" else f"qc-prod-{role}"
    app = read(ROOT / "argocd" / f"{name}.yaml")
    spec = app["spec"]
    source = spec["source"]
    # 서로 다른 클러스터에 존재하는 기존 namespace 이름은 동일합니다.
    require(app["metadata"] == {"name": name, "namespace": "argocd"}, f"{name}: identity")
    require(spec["destination"] == {"namespace": "quantum-cloud-prod", "server": "https://kubernetes.default.svc"},
            f"{name}: existing destination")
    require(source["targetRevision"] == environment, f"{name}: branch")
    require(source["path"] == f"charts/qc-prod-{role}", f"{name}: chart")
    values = source["helm"]["valueFiles"]
    policy = spec["syncPolicy"]
    require("CreateNamespace=false" in policy["syncOptions"], f"{name}: namespace ownership")
    if environment == "dev":
        require(not any("nks-" in p for p in values), f"{name}: on-prem cannot load NKS overlays")
        if role == "app":
            require(policy["automated"]["selfHeal"] is True and policy["automated"]["prune"] is False,
                    "on-prem dev app selfHeal/prune contract")
            prefix = ["prod-app", "prod-app-images", "prod-app-online", "dev-dns"]
            require(values[:4] == [f"../../values/{v}.yaml" for v in prefix], "on-prem dev merge order")
            require(len(values) == 12 and {Path(p).stem for p in values[4:]} == COMPONENTS and
                    all(p.startswith("../../values/onprem-dev-images/") for p in values[4:]),
                    "on-prem Argo must load all 8 isolated image files last")
            require(not any(item["name"] == "modulePublish.enabled"
                            for item in source["helm"].get("parameters", [])) and
                    "modulePublish" not in source["helm"].get("valuesObject", {}),
                    "on-prem publisher activation must come from environment values")
        else:
            require(values == ["../../values/prod-stateful.yaml"] and "automated" not in policy,
                    "existing on-prem stateful values and manual sync must remain intact")
    else:
        require(not any("onprem-dev" in p or "dev-dns" in p for p in values), "prod values isolation")
        require(policy["automated"]["prune"] is False, "prod prune must stay disabled")
        if role == "app":
            parameters = {item["name"]: item["value"] for item in source["helm"].get("parameters", [])}
            require(parameters.get("modulePublish.enabled") == "false" and
                    parameters.get("apps.setupRegistry.replicas") == "0",
                    "prod publisher and Registry pause parameters must remain intact")
    docs = render(app)
    if environment == "prod":
        CHECK.check_common(docs, f"prod-{role}")
        if role == "stateful":
            CHECK.check_stateful(docs, "prod")
    if role == "app":
        check_app(docs, environment)
        if environment == "dev":
            enabled = read(ROOT / "values/onprem-dev-images/modulePublisher.yaml")["modulePublish"]["enabled"]
            require(isinstance(enabled, bool), "publisher enabled value must be boolean")
            require(bool(check_dev_publisher(docs)) == enabled, "publisher must follow environment values")
            check_onprem_preservation(app, docs)
    else:
        require(set(CHECK.resources(docs, "Namespace")) == {"quantum-cloud-prod"}, "stateful namespace ownership")
        require(CHECK.resources(docs, "InnoDBCluster")["qc-mysql"]["spec"]["secretName"] == "qc-mysql-root",
                "existing DB root Secret contract")
    print(f"PASS Argo {name}: actual values order, namespace, images, origin and sync policy")
    return app


def check_app(docs, environment):
    deployments = CHECK.resources(docs, "Deployment")
    require(len(deployments) == 7, f"{environment}: seven application deployments")
    require(not CHECK.resources(docs, "Namespace"), "app must not own namespace")
    if environment == "dev":
        check_dev_publisher(docs)
    else:
        require(not CHECK.resources(docs, "Job"), "prod publisher must remain paused")
    require(len(CHECK.resources(docs, "HTTPRoute")) == (2 if environment == "dev" else 0), "Gateway/ALB contract")
    CHECK.check_job_service_credentials(docs, "qc-prod-app-secrets")
    for deployment in deployments.values():
        require(deployment["metadata"]["namespace"] == "quantum-cloud-prod", "existing app namespace")
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            require(re.fullmatch(r"ghcr\.io/quantum-platform-kist/[^@]+@sha256:[0-9a-f]{64}", container["image"]),
                    "application image must use immutable GHCR digest")
        for key, value in CHECK.entries(deployment):
            if key == "secretKeyRef":
                require(value["name"] == "qc-prod-app-secrets", "existing app Secret")
    if environment == "dev":
        require(all(d["spec"]["replicas"] == 1 for d in deployments.values()), "on-prem dev remains seven replicas")
        CHECK.check_dev_origins(docs)
        routes = CHECK.resources(docs, "HTTPRoute")
        require(set(routes) == {"qc-shell", "qc-modules"}, "existing route names must remain intact")
        for name, section, backend in [("qc-shell", "qc-dev-shell-https", "shell"),
                                       ("qc-modules", "qc-dev-modules-https", "setup-modules")]:
            route = routes[name]
            require(route["metadata"]["namespace"] == "quantum-cloud-prod", "existing route namespace")
            require(route["spec"]["parentRefs"] == [{"group": "gateway.networking.k8s.io",
                    "kind": "Gateway", "name": "main-gateway", "namespace": "gateway-system",
                    "sectionName": section}], f"{name}: must attach to the existing shared Gateway's dev listener")
            require(route["spec"]["rules"][0]["backendRefs"] == [{"name": backend, "port": 8080}],
                    f"{name}: existing service backend")
        for deployment in deployments.values():
            require(deployment["spec"]["template"]["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "prod-k8s-03"},
                    "existing on-prem app node")
        auth = CHECK.environment(docs, "auth")
        require(auth["TRUSTED_PROXIES"]["value"] == "10.244.2.0/24", "on-prem proxy contract")
        for config in CHECK.resources(docs, "ConfigMap").values():
            require("resolver 10.96.0.10 " in config["data"]["default.conf"], "on-prem DNS contract")
        registry = CHECK.environment(docs, "setup-registry")
        require(registry["MODULE_STORAGE_BUCKET"]["value"] == "setup-modules" and
                registry["MODULE_STORAGE_ENDPOINT"]["value"] == "http://qc-minio:9000", "existing MinIO and bucket")
    else:
        require(deployments["setup-registry"]["spec"]["replicas"] == 0, "prod registry pause must be preserved")


def check_dev_publisher(docs):
    jobs = [doc for doc in docs if doc["kind"] == "Job"]
    require(len(jobs) <= 1, "on-prem app may only add one publisher Job")
    if not jobs:
        return None
    job = jobs[0]
    metadata = job["metadata"]
    require(job["apiVersion"] == "batch/v1" and metadata["name"] == PUBLISHER_NAME and
            metadata["namespace"] == "quantum-cloud-prod", "only the named on-prem publisher may be excluded")
    annotations = metadata.get("annotations", {})
    require(annotations.get("argocd.argoproj.io/hook") == "PostSync" and
            annotations.get("argocd.argoproj.io/hook-delete-policy") == "BeforeHookCreation,HookSucceeded",
            "publisher must remain a PostSync hook")
    publish = read(ROOT / "values/onprem-dev-images/modulePublisher.yaml")["modulePublish"]
    image = publish["image"]
    require(image["repository"] == PUBLISHER_REPOSITORY and
            re.fullmatch(r"sha256:[0-9a-f]{64}", image["digest"]) and image["tag"].startswith("dev-"),
            "only the immutable dev publisher repository is allowed")
    fingerprint = publish.get("sourceFingerprint", "")
    require(re.fullmatch(r"[0-9a-f]{64}", fingerprint) and
            re.fullmatch(r"onprem-dev-" + fingerprint[:12] + r"-[0-9]+-[0-9]+", publish["version"]) and
            annotations.get("quantum-cloud.kist/module-version") == publish["version"],
            "publisher must use the version from the dev source fingerprint")
    pod = job["spec"]["template"]["spec"]
    require(len(pod["containers"]) == 1 and not pod.get("initContainers"), "publisher container identity")
    container = pod["containers"][0]
    require(container["name"] == "publish" and
            container["image"] == PUBLISHER_REPOSITORY + "@" + image["digest"],
            "publisher must use the selected immutable dev image")
    env = {item["name"]: item for item in container["env"]}
    require(len(env) == len(container["env"]), "publisher environment names must be unique")
    require(env.get("NKS_DEPLOYMENT_ENVIRONMENT") == {"name": "NKS_DEPLOYMENT_ENVIRONMENT", "value": "dev"},
            "publisher environment must be dev")
    require(env.get("NKS_POD_NAMESPACE") == {"name": "NKS_POD_NAMESPACE", "valueFrom": {
        "fieldRef": {"fieldPath": "metadata.namespace"}}}, "publisher must use its existing pod namespace")
    for name in ["LOCAL_ADMIN_EMAIL", "LOCAL_ADMIN_PASSWORD"]:
        require(env.get(name) == {"name": name, "valueFrom": {"secretKeyRef": {
            "name": "qc-prod-app-secrets", "key": name}}}, "existing publisher Secret and key references")
    return job


def check_onprem_preservation(app, actual):
    before = deepcopy(app)
    before["spec"]["source"]["helm"] = {"valueFiles": [
        "../../values/prod-app.yaml", "../../values/prod-app-images.yaml", "../../values/prod-app-online.yaml"]}
    previous = render(before)
    # 검증한 단일 dev 게시 Job 객체만 제외합니다. 다른 Job/리소스는 숨기지 않습니다.
    publisher = check_dev_publisher(actual)
    normalized = deepcopy([doc for doc in actual if doc is not publisher])
    old_deployments = CHECK.resources(previous, "Deployment")
    for name, deployment in CHECK.resources(normalized, "Deployment").items():
        old_containers = old_deployments[name]["spec"]["template"]["spec"]["containers"]
        for current, old in zip(deployment["spec"]["template"]["spec"]["containers"], old_containers):
            current["image"] = old["image"]
    old_routes = CHECK.resources(previous, "HTTPRoute")
    for name, expected in [("qc-shell", "qc-dev-shell-https"), ("qc-modules", "qc-dev-modules-https")]:
        parent = CHECK.resources(normalized, "HTTPRoute")[name]["spec"]["parentRefs"][0]
        require(parent["sectionName"] == expected, f"{name}: only the intended dev listener may be normalized")
        parent["sectionName"] = old_routes[name]["spec"]["parentRefs"][0]["sectionName"]
    normalized_text = yaml.safe_dump(normalized)
    for current, old in [("modules.qc-dev.quantum-instrument.com", "modules.qc.quantum-instrument.com"),
                         ("qc-dev.quantum-instrument.com", "qc.quantum-instrument.com")]:
        normalized_text = normalized_text.replace(current, old)
    require(normalized_text == yaml.safe_dump(previous),
            "on-prem change exceeds images, public origins, two dev listeners and the validated dev publisher")
    print("PASS on-prem full render equivalence: images, dev origins, two dev listeners and validated publisher only")


def check_seed_values():
    directory = ROOT / "values/onprem-dev-images"
    require({p.stem for p in directory.glob("*.yaml")} == COMPONENTS, "dev needs 8 image overrides")
    for path in directory.glob("*.yaml"):
        data = read(path)
        if path.stem == "modulePublisher":
            require(set(data) == {"modulePublish"}, "publisher must not override unrelated settings")
            image = data["modulePublish"]["image"]
            fingerprint = data["modulePublish"].get("sourceFingerprint")
        else:
            require(set(data) == {"images"} and set(data["images"]) == {path.stem}, "component ownership")
            image = data["images"][path.stem]
            fingerprint = image.get("sourceFingerprint")
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", image["digest"]), f"{path.name}: immutable digest")
        if image["tag"].startswith("prod-"):
            require(fingerprint is None, f"{path.name}: prod seed must not satisfy dev dedup")
            if path.stem == "modulePublisher":
                require(data["modulePublish"]["enabled"] is False, "prod publisher seed cannot be active")
    print("PASS on-prem dev image ownership, immutable digest and seed fingerprint isolation")


def check_publisher(app):
    for enabled in [False, True]:
        docs = render(app, {"modulePublish.enabled": str(enabled).lower()})
        require(bool(check_dev_publisher(docs)) == enabled, "publisher off/on must control only the expected Job")
        check_app(docs, "dev")
        check_onprem_preservation(app, docs)
        print(f"PASS on-prem publisher {'on' if enabled else 'off'}: full app preservation and publisher contract")


def main():
    check_seed_values()
    for environment in ["prod", "dev"]:
        for role in ["stateful", "app"]:
            app = check_application(environment, role)
            if environment == "dev" and role == "app":
                check_publisher(app)


if __name__ == "__main__":
    main()
