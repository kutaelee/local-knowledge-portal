from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["keyword", "semantic", "hybrid", "path", "symbol"] = "hybrid"
    top_k: int = Field(default=10, ge=1, le=100)
    source_root_id: UUID | None = None
    project: str | None = None
    path_prefix: str | None = None
    embedding_revision: str | None = None
    minimum_similarity: float = Field(default=0.2, ge=-1, le=1)


class Provenance(BaseModel):
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    source_root: str
    canonical_path: str
    relative_path: str
    start_line: int
    end_line: int
    content_hash: str
    indexed_timestamp: str


class SearchResult(BaseModel):
    title: str
    heading_or_symbol: str | None
    snippet: str
    lexical_rank: float | None
    vector_similarity: float | None
    fused_rank: float
    match_reason: list[str]
    provenance: Provenance


class SearchResponse(BaseModel):
    query: str
    mode: str
    confidence: Literal["high", "low", "none"]
    results: list[SearchResult]
    total: int


class RagRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=30)
    max_chars: int = Field(default=12000, ge=1000, le=100000)
    filters: dict[str, Any] = Field(default_factory=dict)
