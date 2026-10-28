#!/usr/bin/env python3
"""NKS values 병합·저장소·배치 계약을 검증합니다. 클러스터/Secret에는 접근하지 않습니다."""

from copy import deepcopy
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

import yaml


ROOT = Path(__file__).resolve().parent.parent
STORAGE_CLASS = "nks-block-storage-retain"
OBJECT_ENDPOINT = "https://kr.object.gov-ncloudstorage.com"
PROFILES = {
    "prod-stateful": ["prod-stateful", "nks-prod-stateful"],
    "dev-stateful": ["prod-stateful", "dev-stateful", "nks-dev-stateful"],
    "prod-app": ["prod-app", "prod-app-images", "nks-prod-app"],
    "prod-app-online": ["prod-app", "prod-app-images", "nks-prod-app", "prod-app-online"],
    "dev-app": ["prod-app", "dev-app", "prod-app-images", "nks-dev-app"],
}
SITE_GLOBAL = {
    # 문서용 주소이며 실제 NKS 주소를 의미하지 않습니다.
    "dnsResolver": "192.0.2.53",
    "trustedProxies": "198.51.100.0/24",
    "hosts": {"shell": "qc.example.invalid", "modules": "modules.qc.example.invalid"},
    "issuer": "https://qc.example.invalid",
    "gateway": {
        "name": "site-gateway",
        "namespace": "site-gateway-system",
        "shellSectionName": "site-shell-https",
        "moduleSectionName": "site-modules-https",
    },
}


CONFIG_CHECKSUM = "checksum/nginx-config"
STATIC_APPS = ("shell", "setup-modules")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def command(profile, extra=(), site=None, action="template"):
    chart = "stateful" if profile.endswith("stateful") else "app"
    environment = profile.split("-")[0]
    args = ["helm", action]
    if action == "template":
        args.append(f"qc-{environment}-{chart}")
    args += [str(ROOT / "charts" / f"qc-prod-{chart}"),
             "--namespace", f"quantum-cloud-{environment}"]
    for name in PROFILES[profile]:
        args += ["-f", str(ROOT / "values" / f"{name}.yaml")]
    if site:
        args += ["-f", str(site)]
    return args + list(extra)


def run(args):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=30)


def render(profile, extra=(), site=None, save=False):
    result = run(command(profile, extra, site))
    require(result.returncode == 0, f"렌더 실패: {profile}\n{result.stderr}")
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]
    require(docs and all(isinstance(doc, dict) for doc in docs),
            f"{profile}: 유효한 YAML 리소스가 필요합니다")
    if save:
        output = ROOT / "rendered"
        output.mkdir(exist_ok=True)
        (output / f"qc-nks-{profile}.yaml").write_text(result.stdout)
    return docs


def resources(docs, kind):
    return {doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == kind}


def entries(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from entries(child)
    elif isinstance(value, list):
        for child in value:
            yield from entries(child)


def check_placement(pod, role, label):
    require(pod.get("nodeSelector") == {"qc.quantum-cloud/role": role},
            f"{label}: {role} nodeSelector가 필요합니다")
    require(not pod.get("affinity"), f"{label}: 기존 affinity가 남았습니다")
    expected = ([{"key": "qc.quantum-cloud/dedicated", "operator": "Equal",
                  "value": "data", "effect": "NoSchedule"}] if role == "data" else [])
    require((pod.get("tolerations") or []) == expected,
            f"{label}: {role} toleration 계약이 다릅니다")


def check_common(docs, profile):
    namespace = f"quantum-cloud-{profile.split('-')[0]}"
    stateful = profile.endswith("stateful")
    require(set(resources(docs, "Namespace")) == ({namespace} if stateful else set()),
            f"{profile}: Namespace는 stateful만 소유해야 합니다")
    prohibited = {"Node", "StorageClass", "PersistentVolume", "Gateway", "Ingress", "Secret"}
    require(not any(doc["kind"] in prohibited for doc in docs),
            f"{profile}: 차트가 사전 준비 리소스를 생성합니다")
    serialized = yaml.safe_dump(docs)
    for old in ("kubernetes.io/hostname", "prod-k8s-", "fab.kist/", "fab-lvm-retain",
                "10.96.0.10", "10.244.2.0/24"):
        require(old not in serialized, f"{profile}: 기존 클러스터 참조 {old}가 남았습니다")
    for doc in docs:
        kind, name, spec = doc["kind"], doc["metadata"]["name"], doc.get("spec", {})
        if kind != "Namespace":
            require(doc["metadata"].get("namespace") == namespace,
                    f"{profile}/{name}: namespace가 다릅니다")
        if kind == "Service":
            require(spec.get("type", "ClusterIP") == "ClusterIP",
                    f"{profile}/{name}: 내부 서비스여야 합니다")
        if kind in {"StatefulSet", "Deployment", "Job"}:
            check_placement(spec["template"]["spec"],
                            "data" if kind == "StatefulSet" else "app", name)
        elif kind == "CronJob":
            check_placement(spec["jobTemplate"]["spec"]["template"]["spec"], "app", name)
        elif kind == "InnoDBCluster":
            check_placement(spec["podSpec"], "data", name)
            check_placement(spec["router"]["podSpec"], "app", f"{name}/router")


def check_claim(spec, expected_size, label):
    require(spec.get("storageClassName") == STORAGE_CLASS, f"{label}: NKS retain SC가 필요합니다")
    require(spec.get("accessModes") == ["ReadWriteOnce"], f"{label}: RWO만 허용합니다")
    size = spec["resources"]["requests"]["storage"]
    match = re.fullmatch(r"([1-9][0-9]*)Gi", size)
    require(match and int(match[1]) >= 10 and int(match[1]) % 10 == 0,
            f"{label}: 10Gi 이상·10Gi 단위여야 합니다: {size}")
    require(size == expected_size, f"{label}: 예상 용량 {expected_size}, 실제 {size}")
    require("volumeName" not in spec and "selector" not in spec,
            f"{label}: 기존 PV에 바인딩하면 안 됩니다")


def check_stateful(docs, environment, backup=False, minio=False):
    mysql = resources(docs, "InnoDBCluster")
    require(set(mysql) == {"qc-mysql"}, "MySQL InnoDBCluster 하나가 필요합니다")
    mysql_spec = mysql["qc-mysql"]["spec"]
    require(mysql_spec["instances"] == 1 and mysql_spec["router"]["instances"] == 1,
            "MySQL 인스턴스와 router 계약이 바뀌었습니다")
    require(mysql_spec["secretName"] == ("qc-mysql-root" if environment == "prod"
                                          else "qc-dev-mysql-root"),
            "운영/개발 MySQL root Secret 참조가 다릅니다")
    check_claim(mysql_spec["datadirVolumeClaimTemplate"],
                "80Gi" if environment == "prod" else "20Gi", "MySQL CR datadir")
    sizes = {"qc-kafka": "40Gi" if environment == "prod" else "10Gi", "qc-redis": "10Gi"}
    if minio:
        sizes["qc-minio"] = "60Gi" if environment == "prod" else "10Gi"
    statefulsets = resources(docs, "StatefulSet")
    require(set(statefulsets) == set(sizes), "데이터 StatefulSet 구성이 다릅니다")
    for name, size in sizes.items():
        spec = statefulsets[name]["spec"]
        claims = spec["volumeClaimTemplates"]
        require(len(claims) == 1, f"{name}: 데이터 claimTemplate 하나가 필요합니다")
        check_claim(claims[0]["spec"], size, name)
    require(not any(key == "emptyDir" for key, _ in entries(docs)),
            "NKS 데이터 저장소가 persistent여야 합니다")
    jobs = {"qc-mysql-app-bootstrap", "qc-kafka-topic-init"}
    if minio:
        jobs.add("qc-minio-bucket-bootstrap")
    else:
        require(not any("minio" in doc["metadata"]["name"] or
                        "minio" in doc["metadata"].get("labels", {}).get(
                            "app.kubernetes.io/component", "") for doc in docs),
                "Object Storage 프로필에 MinIO 리소스가 남았습니다")
    require(set(resources(docs, "Job")) == jobs, "bootstrap Job 구성이 다릅니다")
    pvcs, cronjobs = resources(docs, "PersistentVolumeClaim"), resources(docs, "CronJob")
    require(set(pvcs) == ({"qc-mysql-logical-backups"} if backup else set()),
            "backup PVC 활성화 계약이 다릅니다")
    require(set(cronjobs) == ({"qc-mysql-logical-backup"} if backup else set()),
            "backup CronJob 활성화 계약이 다릅니다")
    if backup:
        check_claim(pvcs["qc-mysql-logical-backups"]["spec"], "20Gi", "backup")
    # 모든 storageClassName을 세어 추가 PVC/중첩 claimTemplate도 놓치지 않습니다.
    require(sum(key == "storageClassName" for key, _ in entries(docs)) ==
            3 + int(backup) + int(minio), "예상하지 않은 데이터 claim이 있습니다")


def environment(docs, name):
    deployment = resources(docs, "Deployment")[name]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {item["name"]: item for item in container.get("env", [])}


def check_job_service_credentials(docs, secret, setup_id="stp_nv_a1"):
    env = environment(docs, "job-orchestrator")
    require(env["JOB_ORCHESTRATOR_AUTH_CLIENT_ID"]["value"] == f"setup-be:{setup_id}",
            "Job must reuse the configured setup service-account identity")
    require(env["JOB_ORCHESTRATOR_AUTH_TOKEN_URI"]["value"] == "http://auth:8081/oauth2/token",
            "Job must use the official Auth token endpoint")
    key = "JOB_ORCHESTRATOR_AUTH_CLIENT_SECRET"
    require(env[key] == {"name": key, "valueFrom": {"secretKeyRef": {"name": secret, "key": key}}},
            "Job client secret must reference the existing Secret key")
    require(env["QUANTUM_SETUP_SECURITY_CLIENT_SECRET"]["valueFrom"] == env[key]["valueFrom"],
            "Registry read and existing Auth clients must reuse the same account secret")
    require("SETUP_REGISTRY_SERVICE_TOKEN" not in env,
            "Job must not use a static Registry bearer token")


def check_app(docs, profile, endpoint=OBJECT_ENDPOINT, routes=False):
    deployments = resources(docs, "Deployment")
    require(set(deployments) == {"auth", "gateway", "setup-registry", "job-orchestrator",
                                 "setup-a-be", "shell", "setup-modules"},
            "앱 Deployment 7개가 필요합니다")
    replicas = 0 if profile == "prod-app" else 1
    require(all(d["spec"]["replicas"] == replicas for d in deployments.values()),
            f"{profile}: 앱 replica가 {replicas}여야 합니다")
    for name in ("job-orchestrator", "setup-a-be"):
        require(deployments[name]["spec"]["strategy"] == {"type": "Recreate"},
                f"{name}: singleton Recreate 계약이 다릅니다")
    require(not resources(docs, "HorizontalPodAutoscaler"), "singleton HPA는 비활성 상태여야 합니다")
    images = yaml.safe_load((ROOT / "values/prod-app-images.yaml").read_text())["images"]
    expected = {item["repository"] + "@" + item["digest"] for item in images.values()}
    actual = {c["image"] for doc in deployments.values()
              for c in doc["spec"]["template"]["spec"]["containers"]}
    require(len(expected) == 7 and actual == expected, "기존 이미지 digest 7개가 바뀌었습니다")
    env = environment(docs, "setup-registry")
    require(env["MODULE_STORAGE_ENDPOINT"]["value"] == endpoint, "Object endpoint가 다릅니다")
    bucket = "kist-obj-dev" if profile.startswith("dev-") else "kist-obj"
    require(env["MODULE_STORAGE_BUCKET"]["value"] == bucket, "운영/개발 버킷이 다릅니다")
    secret = "qc-dev-app-secrets" if profile.startswith("dev-") else "qc-prod-app-secrets"
    check_job_service_credentials(docs, secret)
    for key in ("MODULE_STORAGE_ACCESS_KEY", "MODULE_STORAGE_SECRET_KEY"):
        require(env[key] == {"name": key, "valueFrom": {"secretKeyRef": {"name": secret, "key": key}}},
                f"{key}: 기존 Secret key 참조를 유지해야 합니다")
    require("MODULE_STORAGE_REGION" not in env, "지원하지 않는 region env를 추가하면 안 됩니다")
    auth = environment(docs, "auth")
    require(auth["MAIL_DELIVERY_MODE"]["value"] == "mock", "현재 메일 모드는 mock이어야 합니다")
    require(auth["TRUSTED_PROXIES"]["value"] == SITE_GLOBAL["trustedProxies"],
            "현장 proxy CIDR이 전달되지 않았습니다")
    for config in resources(docs, "ConfigMap").values():
        require(f"resolver {SITE_GLOBAL['dnsResolver']} " in config["data"]["default.conf"],
                "현장 DNS resolver가 nginx에 전달되지 않았습니다")
    check_shell_setup_api(docs)
    config_checksums(docs)
    rendered_routes = resources(docs, "HTTPRoute")
    require(len(rendered_routes) == (2 if routes else 0), "HTTPRoute 기본 비활성/현장 활성화 계약이 다릅니다")
    if routes:
        for name, host, listener in (("qc-shell", "shell", "shellSectionName"),
                                     ("qc-modules", "modules", "moduleSectionName")):
            spec = rendered_routes[name]["spec"]
            require(spec["hostnames"] == [SITE_GLOBAL["hosts"][host]], "현장 hostname이 다릅니다")
            parent = spec["parentRefs"][0]
            require(parent["name"] == SITE_GLOBAL["gateway"]["name"] and
                    parent["namespace"] == SITE_GLOBAL["gateway"]["namespace"] and
                    parent["sectionName"] == SITE_GLOBAL["gateway"][listener],
                    "현장 Gateway/listener가 전달되지 않았습니다")


def check_publisher_credentials(site):
    options = ["--set", "modulePublish.enabled=true",
               "--set-string", "modulePublish.version=credentials-check",
               "--set-string", "modulePublish.image.repository=ghcr.io/example/module-publisher",
               "--set-string", "modulePublish.image.digest=sha256:" + "0" * 64]
    configured = render("prod-app-online", options, site)
    configured_env = resources(configured, "Job")["qc-nv-module-publish"]["spec"]["template"]["spec"]["containers"][0]["env"]
    for entry in configured_env:
        if entry["name"] in ("LOCAL_ADMIN_EMAIL", "LOCAL_ADMIN_PASSWORD"):
            require(entry["valueFrom"]["secretKeyRef"]["name"] == "qc-nv-module-publisher-auth",
                    "NKS prod publisher must use its separate login Secret")
    for profile in ("prod-app-online", "dev-app"):
        default_docs = render(profile, options + [
            "--set-string", "modulePublish.credentialsSecretName="], site)
        custom_docs = render(profile, options + [
            "--set-string", "modulePublish.credentialsSecretName=publisher-login"], site)
        default_secret = "qc-dev-app-secrets" if profile == "dev-app" else "qc-prod-app-secrets"
        for docs, expected_secret in [(default_docs, default_secret),
                                      (custom_docs, "publisher-login")]:
            job = resources(docs, "Job")["qc-nv-module-publish"]
            env = {entry["name"]: entry for entry in job["spec"]["template"]["spec"]["containers"][0]["env"]}
            for name in ("LOCAL_ADMIN_EMAIL", "LOCAL_ADMIN_PASSWORD"):
                require(env[name] == {"name": name, "valueFrom": {"secretKeyRef": {
                    "name": expected_secret, "key": name}}},
                    f"{profile}: publisher login must use the selected Secret")
                env[name]["valueFrom"]["secretKeyRef"]["name"] = "normalized-publisher-login"
        require(default_docs == custom_docs,
                f"{profile}: publisher credentials override changed unrelated resources")
    print("PASS publisher default/custom Secret references and unrelated resource preservation")


def check_profile_inputs():
    for name in ("nks-prod-stateful", "nks-dev-stateful", "nks-prod-app", "nks-dev-app"):
        values = yaml.safe_load((ROOT / "values" / f"{name}.yaml").read_text())
        for placement in values["global"]["nodePlacement"].values():
            require("kubernetes.io/hostname" in placement["nodeSelector"] and
                    placement["nodeSelector"]["kubernetes.io/hostname"] is None and
                    "affinity" in placement and placement["affinity"] is None,
                    f"{name}: 기존 selector/affinity 제거는 명시적 null이어야 합니다")
        if name.endswith("app"):
            require(values["global"]["dnsResolver"] == "" and
                    values["global"]["trustedProxies"] == "", f"{name}: 기존 DNS/proxy 상속 금지")


def reject(label, profile, extra, site, reason):
    result = run(command(profile, extra, site))
    require(result.returncode != 0, f"잘못된 설정을 허용했습니다: {label}")
    require(reason.lower() in result.stderr.replace("/", ".").lower(), f"예상하지 않은 거부 사유: {label}\n{result.stderr}")
    print(f"PASS NKS 거부: {label} ({reason})")


def config_checksums(docs):
    deployments = resources(docs, "Deployment")
    checksums = {name: deployments[name]["spec"]["template"]["metadata"].get("annotations", {}).get(CONFIG_CHECKSUM, "")
                 for name in STATIC_APPS}
    require(all(re.fullmatch(r"[0-9a-f]{64}", value) for value in checksums.values()),
            "subPath nginx 설정을 쓰는 Deployment에는 설정 checksum이 필요합니다")
    return checksums


def check_config_checksum_rollout(site):
    # subPath로 마운트한 nginx 설정은 실행 중인 Pod에 반영되지 않으므로, 설정이 바뀌면 Pod template도 바뀌어야 합니다.
    base = render("prod-app-online", site=site)
    changed = render("prod-app-online", ["--set-string", "global.dnsResolver=192.0.2.54"], site)
    require(all(config_checksums(base)[name] != config_checksums(changed)[name] for name in STATIC_APPS),
            "nginx 설정 변경이 shell/setup-modules Pod template에 반영되지 않습니다")
    others = lambda docs: {name: doc for name, doc in resources(docs, "Deployment").items() if name not in STATIC_APPS}
    require(others(base) == others(changed), "nginx 설정 checksum이 다른 Deployment를 바꾸면 안 됩니다")
    print("PASS nginx 설정 checksum: 설정 변경 시 shell/setup-modules만 재시작")


def check_shell_setup_api(docs, setup_id="stp_nv_a1"):
    config = " ".join(resources(docs, "ConfigMap")["shell-nginx"]["data"]["default.conf"].split())
    route = ("location ~ ^/module/setup-a/((?:setups/" + setup_id + "/setup-a|local-rbac)(?:/.*)?)$ {")
    block = config.split(route, 1)[1].split(" }", 1)[0] if route in config else ""
    require("location /module/setup-a/ { return 404; }" in config and
            'proxy_set_header Cookie "";' in block and "proxy_hide_header Set-Cookie;" in block and
            "rewrite ^/module/setup-a/((?:setups/" + setup_id + "/setup-a|local-rbac)(?:/.*)?)$ /$1 break;" in block and
            "proxy_pass http://$setup_a_be_upstream;" in block,
            "Shell origin setup API는 허용 경로만 쿠키 없이 setup-a-be로 보내야 합니다")


def main():
    check_profile_inputs()
    with TemporaryDirectory(prefix="qc-nks-render-") as directory:
        site = Path(directory) / "site-values.yaml"
        site.write_text(yaml.safe_dump({"global": SITE_GLOBAL}))
        for profile in PROFILES:
            selected_site = None if profile.endswith("stateful") else site
            lint = run(command(profile, site=selected_site, action="lint"))
            require(lint.returncode == 0, lint.stdout + lint.stderr)
            docs = render(profile, site=selected_site, save=True)
            check_common(docs, profile)
            if profile.endswith("stateful"):
                check_stateful(docs, profile.split("-")[0])
            else:
                check_app(docs, profile)
            print(f"PASS NKS lint/렌더: {profile} ({' + '.join(PROFILES[profile])})")
        for environment_name in ("prod", "dev"):
            profile = f"{environment_name}-stateful"
            backup = render(profile, ["--set", "backup.enabled=true"])
            check_common(backup, profile)
            check_stateful(backup, environment_name, backup=True)
            minio = render(profile, ["--set", "minio.enabled=true"])
            check_common(minio, profile)
            check_stateful(minio, environment_name, minio=True)
        print("PASS NKS 운영/개발 backup 20Gi retain/RWO·app 배치 및 MinIO 재활성화 60/10Gi")

        for profile in ("prod-app-online", "dev-app"):
            routes = render(profile, ["--set", "httpRoutes.enabled=true"], site)
            check_common(routes, profile)
            check_app(routes, profile, routes=True)
        for endpoint in ("", "https://object.example.invalid:9443/", "http://object.example.invalid:9000",
                         "http://192.0.2.10:9000/", "https://[2001:db8::10]:9443/"):
            docs = render("prod-app-online", ["--set-string", f"apps.setupRegistry.moduleStorageEndpoint={endpoint}"], site)
            check_app(docs, "prod-app-online", endpoint=endpoint or "http://qc-minio:9000")
        custom_service = render("prod-app-online", ["--set-string", "apps.setupRegistry.moduleStorageEndpoint=",
                                                    "--set", "statefulServices.minio=custom-minio"], site)
        check_app(custom_service, "prod-app-online", endpoint="http://custom-minio:9000")
        print("PASS NKS HTTPRoute/site-values·endpoint 그대로 출력·기존 내부 endpoint fallback")
        check_config_checksum_rollout(site)
        check_publisher_credentials(site)

        registry_off = render("prod-app-online", ["--set", "apps.setupRegistry.replicas=0",
                                                  "--set", "modulePublish.enabled=false"], site)
        stopped_deployments = resources(registry_off, "Deployment")
        require(stopped_deployments["setup-registry"]["spec"]["replicas"] == 0,
                "Registry stop override must remain effective")
        require(stopped_deployments["job-orchestrator"]["spec"]["replicas"] == 1,
                "Stopping Registry must not stop Job")
        require(not resources(registry_off, "Job"), "Module publishing must remain disabled")
        check_job_service_credentials(registry_off, "qc-prod-app-secrets")
        custom_setup = render("prod-app-online", ["--set", "global.moduleSetupId=stp_regression"], site)
        check_job_service_credentials(custom_setup, "qc-prod-app-secrets", "stp_regression")
        print("PASS Job service credentials, setup identity, Registry stop and publisher disable overrides")

        for endpoint in ("ftp://object.example.invalid", "https://", "https://bad host", "object.example.invalid",
                         "https://object.example.invalid:9443/storage",
                         "https://object.example.invalid?query=value",
                         "https://object.example.invalid#fragment",
                         "https://user@object.example.invalid",
                         "https://user:password@object.example.invalid",
                         "https://object.example.invalid/?query=value",
                         "https://object.example.invalid/#fragment",
                         r"https://object.example.invalid\storage"):
            escaped_endpoint = endpoint.replace("\\", "\\\\")  # Helm --set-string의 escape 보존
            reject(f"endpoint URI {endpoint!r}", "prod-app",
                   ["--set-string", f"apps.setupRegistry.moduleStorageEndpoint={escaped_endpoint}"], site,
                   "apps.setupRegistry.moduleStorageEndpoint")
        reject("endpoint bool 타입", "prod-app", ["--set", "apps.setupRegistry.moduleStorageEndpoint=true"],
               site, "string")
        reject("빈 bucket", "prod-app", ["--set-string", "apps.setupRegistry.moduleStorageBucket="],
               site, "apps.setupRegistry.moduleStorageBucket")
        reject("bucket bool 타입", "prod-app", ["--set", "apps.setupRegistry.moduleStorageBucket=true"],
               site, "string")
        no_dns = deepcopy(SITE_GLOBAL)
        del no_dns["dnsResolver"]
        missing_dns = Path(directory) / "missing-dns.yaml"
        missing_dns.write_text(yaml.safe_dump({"global": no_dns}))
        for profile in ("prod-app", "dev-app"):
            reject(f"{profile} 현장 DNS 누락", profile, [], missing_dns, "global.dnsResolver")
    print("PASS NKS 정적 출력 계약 검증 완료 (실제 NKS/Object Storage 연동은 별도 검증 필요)")


if __name__ == "__main__":
    main()
