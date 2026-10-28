-- Reviewed manual SQL for setup_registry: serve the NV setup API under the Shell origin.
-- Do not run through GitOps. Same convention as FAB EBL (/module/ebl): the Shell only calls
-- manifest directApiBaseUrl + "/local-rbac" when that URL shares the Shell origin.
--
-- Apply on one environment only after both are live there:
--   1. Shell nginx /module/setup-a/ route (charts/qc-prod-app shell-nginx).
--   2. setup-a-be with local-rbac at /local-rbac.
-- New module activations inherit these values from the active manifest, and
-- ci/nks/module_publisher.py publishes only when they match exactly.
-- Each statement only changes the row that still carries that environment's
-- previous modules-origin URL, so running the other environment's statement is a no-op.

-- On-premises dev (qc-dev).
UPDATE setup_module_manifests
SET
  direct_api_base_url = 'https://qc-dev.quantum-instrument.com/module/setup-a',
  connect_src = '["https://qc-dev.quantum-instrument.com","wss://qc-dev.quantum-instrument.com"]',
  updated_who = 'shell-origin-api-reviewed-sql'
WHERE setup_id = 'stp_nv_a1'
  AND active = TRUE
  AND trust_level = 'FIRST_PARTY_TRUSTED'
  AND token_policy = 'PLATFORM_JWT'
  AND direct_api_base_url = 'https://modules.qc-dev.quantum-instrument.com/api/setup-a';

-- NKS prod. Run only when the prod branch carries the same chart and publisher change.
UPDATE setup_module_manifests
SET
  direct_api_base_url = 'https://qc.quantum-instrument.com/module/setup-a',
  connect_src = '["https://qc.quantum-instrument.com","wss://qc.quantum-instrument.com"]',
  updated_who = 'shell-origin-api-reviewed-sql'
WHERE setup_id = 'stp_nv_a1'
  AND active = TRUE
  AND trust_level = 'FIRST_PARTY_TRUSTED'
  AND token_policy = 'PLATFORM_JWT'
  AND direct_api_base_url = 'https://modules.qc.quantum-instrument.com/api/setup-a';

-- Rollback restores the previous modules-origin values for the same environment, for example dev:
-- UPDATE setup_module_manifests
-- SET direct_api_base_url = 'https://modules.qc-dev.quantum-instrument.com/api/setup-a',
--     connect_src = '["https://modules.qc-dev.quantum-instrument.com","wss://modules.qc-dev.quantum-instrument.com"]',
--     updated_who = 'shell-origin-api-rollback'
-- WHERE setup_id = 'stp_nv_a1' AND active = TRUE
--   AND direct_api_base_url = 'https://qc-dev.quantum-instrument.com/module/setup-a';

SELECT setup_id, manifest_id, version, direct_api_base_url, connect_src
FROM setup_module_manifests
WHERE setup_id = 'stp_nv_a1' AND active = TRUE;
