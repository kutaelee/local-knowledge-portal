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
  projects: number;
  documents: number;
  chunks: number;
  jobs: Record<string, number>;
  oldest_pending_seconds: number;
  workers: number;
  embedding_model: string;
  embedding_revision: string;
  pipeline_version: string;
};

export type TreeItem = {
  id: string;
  source_root_id: string;
  project: string;
  path: string;
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
