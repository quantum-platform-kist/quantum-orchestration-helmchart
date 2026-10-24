"""Closed on-premises dev / NKS prod contracts, shared by CI and publisher."""
import os


CONTRACTS = {
    "prod": {
        "branch": "prod", "valuesDirectory": "values/nks-prod-images",
        "workflow": "prod-nks.yml", "namespace": "quantum-cloud-prod",
        "appSecret": "qc-prod-app-secrets", "moduleVersionPrefix": "nks-",
        "origin": "https://qc.quantum-instrument.com",
        "moduleOrigin": "https://modules.qc.quantum-instrument.com",
    },
    "dev": {
        "branch": "dev", "valuesDirectory": "values/onprem-dev-images",
        "workflow": "dev-nks.yml", "namespace": "quantum-cloud-prod",
        "appSecret": "qc-prod-app-secrets", "moduleVersionPrefix": "onprem-dev-",
        "origin": "https://qc-dev.quantum-instrument.com",
        "moduleOrigin": "https://modules.qc-dev.quantum-instrument.com",
    },
}


def deployment_contract(value=None):
    name = os.environ.get("NKS_DEPLOYMENT_ENVIRONMENT", "prod") if value is None else value
    if not isinstance(name, str) or name not in CONTRACTS:
        raise RuntimeError("deployment_environment must be dev or prod")
    return {"name": name, **CONTRACTS[name]}
