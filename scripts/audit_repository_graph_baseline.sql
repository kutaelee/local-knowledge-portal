\pset pager off

SELECT clock_timestamp() AS captured_at_utc,
       version_num AS alembic_revision
FROM alembic_version;

WITH latest AS (
  SELECT DISTINCT ON (project_id)
         id, project_id, source_hash, git_commit, git_branch,
         dirty_worktree, status, stale, created_at
  FROM repository_snapshot
  ORDER BY project_id, created_at DESC, id DESC
)
SELECT clock_timestamp() AS captured_at_utc,
       p.canonical_name,
       latest.id AS snapshot_id,
       latest.source_hash,
       latest.git_commit,
       latest.git_branch,
       latest.dirty_worktree,
       latest.status,
       latest.stale,
       latest.created_at,
       (SELECT count(*) FROM repository_source_file f
        WHERE f.snapshot_id = latest.id) AS files,
       (SELECT count(*) FROM repository_source_symbol s
        WHERE s.snapshot_id = latest.id) AS symbols,
       (SELECT count(*) FROM repository_source_relation r
        WHERE r.snapshot_id = latest.id) AS relations,
       (SELECT count(*) FROM repository_knowledge_item k
        WHERE k.snapshot_id = latest.id) AS knowledge_items
FROM latest
JOIN repository_project p ON p.id = latest.project_id
ORDER BY p.canonical_name;

WITH latest AS (
  SELECT DISTINCT ON (project_id) id, project_id
  FROM repository_snapshot
  ORDER BY project_id, created_at DESC, id DESC
)
SELECT p.canonical_name,
       f.language,
       r.relation_type,
       r.provenance,
       count(*) AS edge_count
FROM latest
JOIN repository_project p ON p.id = latest.project_id
JOIN repository_source_relation r ON r.snapshot_id = latest.id
LEFT JOIN repository_source_file f
  ON f.snapshot_id = r.snapshot_id
 AND f.relative_path = r.relative_path
GROUP BY p.canonical_name, f.language, r.relation_type, r.provenance
ORDER BY edge_count DESC;

WITH latest AS (
  SELECT DISTINCT ON (project_id) id
  FROM repository_snapshot
  ORDER BY project_id, created_at DESC, id DESC
), grouped AS (
  SELECT r.snapshot_id, r.source_symbol, r.target_symbol, r.relation_type,
         r.provenance, r.relative_path, r.line, count(*) AS copies
  FROM repository_source_relation r
  JOIN latest ON latest.id = r.snapshot_id
  GROUP BY r.snapshot_id, r.source_symbol, r.target_symbol, r.relation_type,
           r.provenance, r.relative_path, r.line
  HAVING count(*) > 1
)
SELECT count(*) AS duplicate_fingerprints,
       coalesce(sum(copies - 1), 0) AS redundant_rows,
       max(copies) AS max_copies
FROM grouped;
