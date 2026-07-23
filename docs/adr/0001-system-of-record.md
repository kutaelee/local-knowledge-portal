# ADR 0001: Filesystem is the system of record

Status: accepted

Source files remain in their existing locations and are read-only to ingestion. PostgreSQL stores metadata, append-only versions, jobs, chunks, and regenerable search indexes. The portal exposes no source-writing API. Generated Markdown is allowed only inside a configured managed directory and must carry managed provenance.

This keeps rollback limited to workers and derived data. It also prevents indexing failures from changing original work.
