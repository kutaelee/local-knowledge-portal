"""Shared SQL invariants for current repository-derived evidence."""

from __future__ import annotations

import re

_SQL_ALIAS = re.compile(r"^[a-z_][a-z0-9_]*$")


def _alias(value: str) -> str:
    if not _SQL_ALIAS.fullmatch(value):
        raise ValueError(f"invalid SQL alias: {value!r}")
    return value


def latest_snapshot_sql(snapshot_alias: str = "s") -> str:
    """Return a fail-closed predicate for the one current project snapshot."""

    snapshot = _alias(snapshot_alias)
    return f"""
        {snapshot}.stale IS FALSE
        AND {snapshot}.id = (
          SELECT latest.id
          FROM repository_snapshot latest
          WHERE latest.project_id = {snapshot}.project_id
          ORDER BY latest.created_at DESC, latest.id DESC
          LIMIT 1
        )
    """


def current_references_sql(knowledge_alias: str = "k") -> str:
    """Require every citation to match a file hash and line range in its snapshot.

    The CASE protects the integer casts from malformed JSON. An empty, non-array,
    malformed, missing, stale-hash, or out-of-bounds reference set is rejected.
    """

    knowledge = _alias(knowledge_alias)
    return f"""
        CASE
          WHEN jsonb_typeof({knowledge}.source_references) = 'array'
           AND jsonb_array_length({knowledge}.source_references) > 0
          THEN NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements({knowledge}.source_references) AS cited(value)
            WHERE NOT (
              jsonb_typeof(cited.value) = 'object'
              AND COALESCE(cited.value->>'file', '') <> ''
              AND COALESCE(cited.value->>'source_hash', '')
                  ~ '^[0-9a-fA-F]{{64}}$'
              AND CASE
                WHEN COALESCE(cited.value->>'start_line', '') ~ '^[1-9][0-9]*$'
                 AND COALESCE(cited.value->>'end_line', '') ~ '^[1-9][0-9]*$'
                THEN EXISTS (
                  SELECT 1
                  FROM repository_source_file source
                  WHERE source.snapshot_id = {knowledge}.snapshot_id
                    AND source.relative_path = cited.value->>'file'
                    AND source.content_hash = cited.value->>'source_hash'
                    AND (cited.value->>'end_line')::bigint
                        >= (cited.value->>'start_line')::bigint
                    AND (cited.value->>'end_line')::bigint <= source.line_count
                )
                ELSE false
              END
            )
          )
          ELSE false
        END
    """
