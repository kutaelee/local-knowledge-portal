export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8010";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

export type Metrics = {
  generated_at: string;
  projects: number;
  documents: number;
  chunks: number;
  semantic_chunks: number;
  semantic_coverage: number;
  document_breakdown: {
    knowledge_documents: number;
    code_files: number;
    support_files: number;
  };
  pending_breakdown: {
    initial_scan: number;
    live_changes: number;
  };
  jobs: Record<string, number>;
  oldest_pending_seconds: number;
  queue_rate_per_hour: number;
  queue_eta_seconds: number | null;
  succeeded_last_3h: number;
  failed_last_hour: number;
  workers: number;
  worker_states: Record<string, number>;
  latest_indexed_at: string | null;
  latest_source_modified_at: string | null;
  throughput: { bucket: string; count: number }[];
  recent_documents: {
    id: string;
    filename: string;
    relative_path: string;
    project: string | null;
    source_root: string;
    modified_at: string;
    indexed_at: string;
    change_type: string;
  }[];
  source_roots: {
    id: string;
    name: string;
    source_type: string;
    document_count: number;
    last_reconciled_at: string | null;
    last_seen_at: string | null;
  }[];
  embedding_model: string;
  embedding_revision: string;
  pipeline_version: string;
  repository_embedding_mode: string;
};

export type TreeItem = {
  id: string;
  source_root_id: string;
  project: string;
  path: string;
  source_relative_path: string;
  state: string;
};

export type SearchResult = {
  title: string;
  heading_or_symbol: string | null;
  snippet: string;
  lexical_rank: number | null;
  vector_similarity: number | null;
  fused_rank: number;
  match_reason: string[];
  provenance: {
    document_id: string;
    document_version_id: string;
    chunk_id: string;
    source_root: string;
    canonical_path: string;
    relative_path: string;
    start_line: number;
    end_line: number;
    content_hash: string;
    indexed_timestamp: string;
  };
};
