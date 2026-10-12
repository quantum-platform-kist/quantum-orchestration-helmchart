-- Reviewed manual SQL for the restored QC setup_registry schema.
-- Do not run through GitOps. Run only after restore validation and before/after
-- setup-a module activation if the active manifest still carries local/dev URLs.
--
-- The enum name PLATFORM_JWT is legacy in the DB model. The current shell/sdk
-- path still uses document-bound setup_scoped token MessagePort exchange; do
-- not inject raw platform JWTs into the iframe.
--
-- module_url keeps the reviewed module version/path under /module-assets and
-- only replaces the restored source origin, for example:
--   http://161.122.93.220:15174/module-assets/...
--   http://127.0.0.1:15174/module-assets/...

UPDATE setup_module_manifests
SET
  module_url = CONCAT(
    'https://modules.qc.quantum-instrument.com',
    SUBSTRING(module_url, LOCATE('/module-assets/', module_url))
  ),
  direct_api_base_url = 'https://modules.qc.quantum-instrument.com/api/setup-a',
  connect_src = '["https://modules.qc.quantum-instrument.com","wss://modules.qc.quantum-instrument.com"]',
  trust_level = 'FIRST_PARTY_TRUSTED',
  token_policy = 'PLATFORM_JWT',
  updated_who = 'prod-cutover-reviewed-sql'
WHERE setup_id = 'stp_nv_a1'
  AND active = TRUE
  AND module_url IS NOT NULL
  AND module_url REGEXP '^https?://[^/]+/module-assets/.+';
