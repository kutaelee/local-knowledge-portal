INSERT INTO source_root (
  id, name, canonical_path, source_type, data_scope, read_only, enabled,
  include_patterns, exclude_patterns, created_at, updated_at
) VALUES (
  '10000000-0000-0000-0000-000000000001',
  'Isolated E2E fixture',
  '/__lkp_isolated_e2e__',
  'validation',
  'validation',
  true,
  false,
  ARRAY['**/*'],
  ARRAY[]::text[],
  now(),
  now()
) ON CONFLICT (canonical_path) DO NOTHING;

INSERT INTO activity_event (
  id, event_key, session_id, turn_id, event_type, occurred_at, project_key,
  cwd, instruction, tool_name, command, exit_code, changed_files,
  document_version_ids, reported_result, verified_result, verification_status,
  metadata, created_at
) VALUES (
  '20000000-0000-0000-0000-000000000001',
  'isolated-e2e-activity',
  'isolated-e2e-session',
  'isolated-e2e-turn',
  'PostToolUse',
  now(),
  'local-knowledge-portal',
  '/workspace',
  'Verify isolated E2E activity details',
  'pytest',
  'pytest tests/e2e',
  0,
  ARRAY['tests/e2e/portal.spec.ts'],
  ARRAY[]::uuid[],
  'E2E reported result',
  'E2E verified result',
  'VERIFIED',
  '{"data_scope":"validation","fixture":"isolated-e2e"}'::jsonb,
  now()
) ON CONFLICT (event_key) DO NOTHING;

INSERT INTO ingest_job (
  id, idempotency_key, source_root_id, canonical_path, job_type, priority,
  status, attempt_count, max_attempts, available_at, finished_at, error_type,
  error_message, error_details, created_at, updated_at
) VALUES (
  '30000000-0000-0000-0000-000000000001',
  'isolated-e2e-dead-letter',
  '10000000-0000-0000-0000-000000000001',
  '/__lkp_isolated_e2e__/failed.md',
  'index',
  999,
  'dead_letter',
  1,
  1,
  now(),
  now(),
  'IsolatedE2EFailure',
  'Intentional isolated E2E retry fixture',
  '{"data_scope":"validation","fixture":"isolated-e2e"}'::jsonb,
  now(),
  now()
) ON CONFLICT (idempotency_key) DO NOTHING;
