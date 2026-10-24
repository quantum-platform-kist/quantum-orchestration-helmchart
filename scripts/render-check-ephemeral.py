#!/usr/bin/env python3
"""클러스터 접속 없이 Helm 출력 계약과 잘못된 입력의 거부를 검증합니다."""

from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parent.parent
NAMESPACE = "quantum-cloud-ephemeral"
PROFILES = {
    "stateful": ["prod-stateful", "ephemeral-stateful"],
    "app": ["prod-app-images", "ephemeral-app"],
}
FORBIDDEN_KINDS = {
    "PersistentVolumeClaim", "PersistentVolume", "StorageClass", "InnoDBCluster",
    "Job", "CronJob", "Gateway", "Ingress", "Secret",
}
FORBIDDEN_KEYS = {
    "persistentVolumeClaim", "volumeClaimTemplates", "datadirVolumeClaimTemplate",
    "volumeClaimTemplate", "storageClassName", "hostPath", "csi", "claimName",
}
OLD_REFERENCES = (
    "quantum-cloud-prod", "quantum-cloud-dev", "prod-k8s-", "dev-k8s-",
    "qc.quantum-instrument.com", "qc-dev.quantum-instrument.com",
    "qc-prod-app-secrets", "qc-dev-app-secrets", "qc-mysql-root", "qc-dev-mysql-root",
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def command(chart, profiles=(), extra=(), namespace=None, action="template"):
    args = ["helm", action]
    if action == "template":
        args.append(f"qc-{'ephemeral' if namespace else 'prod'}-{chart}")
    args.append(str(ROOT / "charts" / f"qc-prod-{chart}"))
    if namespace:
        args += ["--namespace", namespace]
    for profile in profiles:
        args += ["-f", str(ROOT / "values" / f"{profile}.yaml")]
    return args + list(extra)


def run(args):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=30)


def render(chart, profiles=(), extra=(), namespace=None):
    result = run(command(chart, profiles, extra, namespace))
    require(result.returncode == 0, f"렌더 실패: {chart} {profiles}\n{result.stderr}")
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]
    require(docs and all(isinstance(doc, dict) for doc in docs), "유효한 YAML 객체가 필요합니다")
    return docs


def entries(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from entries(child)
    elif isinstance(value, list):
        for child in value:
            yield from entries(child)


def resources(docs, kind):
    return {doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == kind}


def check_common(docs, chart, namespace):
    require(not any(doc["kind"] in FORBIDDEN_KINDS for doc in docs),
            f"{chart}: 임시 모드에 금지된 kind가 있습니다")
    require(not any(key in FORBIDDEN_KEYS for key, _ in entries(docs)),
            f"{chart}: PVC/StorageClass/외부 데이터 볼륨 필드가 있습니다")
    serialized = yaml.safe_dump(docs)
    require(not any(old in serialized for old in OLD_REFERENCES),
            f"{chart}: 운영/dev namespace·hostname·node·Secret 참조가 있습니다")
    namespaces = resources(docs, "Namespace")
    require(set(namespaces) == ({namespace} if chart == "stateful" else set()),
            f"{chart}: Namespace 소유 계약이 다릅니다")
    for doc in docs:
        if doc["kind"] != "Namespace":
            require(doc["metadata"].get("namespace") == namespace,
                    f"{chart}/{doc['metadata']['name']}: namespace가 다릅니다")
        if doc["kind"] in {"StatefulSet", "Deployment"}:
            pod = doc["spec"]["template"]["spec"]
            require(not pod.get("nodeSelector") and not pod.get("affinity")
                    and not pod.get("tolerations"), f"{chart}: 고정 노드 배치가 남았습니다")
        if doc["kind"] == "Service":
            require(doc["spec"].get("type", "ClusterIP") == "ClusterIP",
                    f"{chart}: 서비스가 외부에 노출됩니다")


def check_stateful(docs):
    statefulsets = resources(docs, "StatefulSet")
    paths = {"mysql": "/var/lib/mysql", "kafka": "/var/lib/kafka/data",
             "redis": "/data", "minio": "/data"}
    require(set(statefulsets) == {f"qc-{name}" for name in paths},
            "네 데이터 서비스 StatefulSet이 필요합니다")
    require(sum(key == "emptyDir" for key, _ in entries(docs)) == 4,
            "임시 데이터 emptyDir은 정확히 네 개여야 합니다")
    for name, path in paths.items():
        sts = statefulsets[f"qc-{name}"]
        pod = sts["spec"]["template"]["spec"]
        require(sts["spec"]["replicas"] == 1, f"{name}: 단일 인스턴스가 필요합니다")
        container = next(c for c in pod["containers"] if c["name"] == name)
        mounts = [m for m in container["volumeMounts"] if m["mountPath"] == path]
        require(len(mounts) == 1, f"{name}: 데이터 경로 {path}가 없습니다")
        volume = next(v for v in pod["volumes"] if v["name"] == mounts[0]["name"])
        require("emptyDir" in volume and not volume["emptyDir"].get("medium"),
                f"{name}: 디스크 emptyDir이 필요합니다")
    mysql = resources(docs, "Service")["qc-mysql"]
    require(any(port["port"] == 3306 for port in mysql["spec"]["ports"]),
            "MySQL 앱 서비스는 3306 포트를 유지해야 합니다")


def check_app(docs, pinned=True, routes=False):
    deployments = resources(docs, "Deployment")
    require(len(deployments) == 7 and all(d["spec"]["replicas"] == 1
                                       for d in deployments.values()),
            "앱 Deployment 7개가 각각 replica 1이어야 합니다")
    if pinned:
        images = yaml.safe_load((ROOT / "values/prod-app-images.yaml").read_text())["images"]
        expected = {image["repository"] + "@" + image["digest"] for image in images.values()}
        actual = {c["image"] for d in deployments.values()
                  for c in d["spec"]["template"]["spec"]["containers"]}
        require(len(expected) == 7 and actual == expected, "기존 앱 이미지 digest 7개가 바뀌었습니다")
    rendered_routes = list(resources(docs, "HTTPRoute").values())
    require(len(rendered_routes) == (2 if routes else 0), "HTTPRoute 토글 계약이 다릅니다")
    if routes:
        require({r["spec"]["hostnames"][0] for r in rendered_routes} == {
            "qc-ephemeral.quantum-instrument.com", "modules.qc-ephemeral.quantum-instrument.com"
        }, "임시 HTTPRoute hostname이 다릅니다")
        require(all(r["spec"]["parentRefs"][0]["name"] == "qc-ephemeral-gateway"
                    and r["spec"]["parentRefs"][0]["namespace"] == NAMESPACE
                    for r in rendered_routes), "임시 Gateway 참조가 다릅니다")


def reject(label, chart, profiles, extra, reasons, namespace=NAMESPACE):
    result = run(command(chart, profiles, extra, namespace))
    require(result.returncode != 0, f"잘못된 설정이 허용되었습니다: {label}")
    diagnostic = result.stderr.replace("/", ".").lower().replace("missing property", "required property")
    require(all(reason.lower() in diagnostic for reason in reasons),
            f"예상하지 않은 실패 사유: {label}\n{result.stderr}")
    print(f"PASS 거부: {label} ({'; '.join(reasons)})")


def main():
    matrix = [
        ("stateful", [], []), ("stateful", ["prod-stateful"], []),
        ("stateful", ["prod-stateful"], ["--set", "backup.enabled=true"]),
        ("stateful", ["dev-stateful"], []), ("app", [], []),
        ("app", ["prod-app"], []), ("app", ["prod-app", "prod-app-online"], []),
        ("app", ["prod-app", "prod-app-images", "prod-app-online"], []),
        ("app", ["dev-app"], []),
    ]
    for chart, profiles, extra in matrix:
        render(chart, profiles, extra)
    print("PASS 기존 persistent 9종 렌더")
    for chart, profiles in PROFILES.items():
        lint = run(command(chart, profiles, namespace=NAMESPACE, action="lint"))
        require(lint.returncode == 0, lint.stdout + lint.stderr)
        for selected in (profiles, [f"ephemeral-{chart}"]):
            docs = render(chart, selected, namespace=NAMESPACE)
            check_common(docs, chart, NAMESPACE)
            if chart == "stateful":
                check_stateful(docs)
            else:
                check_app(docs, pinned=len(selected) == 2)
        custom = "qc-render-custom"
        docs = render(chart, profiles, ["--set", f"global.namespace={custom}"], custom)
        check_common(docs, chart, custom)
    routes = render("app", PROFILES["app"], ["--set", "httpRoutes.enabled=true"], NAMESPACE)
    check_common(routes, "app", NAMESPACE)
    check_app(routes, routes=True)
    print("PASS 임시 프로필 lint/렌더·단독 프로필·저장소·격리 참조·앱 이미지·라우트·사용자 namespace")

    for field in ("global.storageClassName", "mysql.storage", "kafka.storage"):
        reject(f"persistent 필수 {field}", "stateful", [], ["--set", f"{field}=null"],
               [*field.split("."), "required"])
    reject("mode enum", "stateful", [], ["--set", "global.storageMode=invalid"],
           ["global.storageMode", "persistent", "ephemeral"])
    reject("mode 타입", "stateful", [], ["--set", "global.storageMode=true"],
           ["global.storageMode", "string"])
    reject("route bool 타입", "app", PROFILES["app"],
           ["--set-string", "httpRoutes.enabled=false"], ["httpRoutes.enabled", "boolean"])
    reject("backup bool 타입", "stateful", PROFILES["stateful"],
           ["--set-string", "backup.enabled=false"], ["backup.enabled", "boolean"])
    cases = [
        ("backup 충돌", "backup.enabled=true", ["cannot enable PVC backup"]),
        ("전체 비활성화에서도 backup 충돌", "backup.enabled=true,mysql.enabled=false,"
         "kafka.enabled=false,redis.enabled=false,minio.enabled=false", ["cannot enable PVC backup"]),
        ("MySQL instances", "mysql.instances=2", ["mysql.instances=1"]),
        ("MySQL routers", "mysql.routers=1", ["mysql.routers=0"]),
        ("Kafka replication", "kafka.topics.stp_nv_a1_experiment_start.replicationFactor=2",
         ["replicationFactor=1"]),
        ("Kafka retention", "kafka.retentionBudgetBytes=1", ["retention budget exceeded"]),
    ]
    for label, value, reasons in cases:
        reject(label, "stateful", PROFILES["stateful"], ["--set", value], reasons)
    reject("운영 namespace", "stateful", PROFILES["stateful"],
           ["--set", "global.namespace=quantum-cloud-prod"],
           ["new namespace", "quantum-cloud-prod"], "quantum-cloud-prod")
    for chart, profiles in PROFILES.items():
        reject(f"{chart} namespace 불일치", chart, profiles, [], ["namespace", "match"], "qc-mismatch")
    print("PASS 임시 모드 출력 계약과 실패 분기 검증 완료")


if __name__ == "__main__":
    main()
