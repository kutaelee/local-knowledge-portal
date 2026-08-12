# ADR 0008: Search latency and CPU budget

Status: accepted

## Context

The first production search query scanned all current and historical chunks because full-text,
path, symbol, trigram, and substring predicates were combined in one `OR`. Semantic and hybrid
search also requested the same query embedding from Ollama every time. Increasing Ollama from one
CPU to two reduced latency but previously drove the container close to two full cores and coincided
with a user-observed 92°C package temperature. The host currently exposes no reliable temperature
sensor to the service.

## Decision

Search uses indexed candidate stages:

1. GIN `tsvector`, path trigram, and exact symbol indexes produce the normal lexical candidates.
2. Only when those stages return no candidate does a trigram word-similarity fallback run.
3. Only when that also returns no candidate does literal content fallback run.
4. Every search transaction has a five-second PostgreSQL statement timeout.

Query embeddings use a revision-aware, process-local TTL/LRU cache. The key includes provider,
model, digest, dimension, and query hash. The cache is bounded to 512 entries for 24 hours by
default. Ollama keeps one model loaded for 24 hours and the API prewarms it at startup. A provider
miss is serialized so duplicate concurrent misses cannot stampede the model.

One CPU remains the safe default for both Ollama and the indexer worker; it is a budget, not a claim
that one core is universally optimal. API is capped at one CPU. Watcher, hook collector, and web are
capped at half a CPU each. Model parallelism and concurrent model requests remain one.

No CPU increase is permitted merely to drain a backlog. A 1.5 CPU Ollama profile may be evaluated
later only when all of the following are available:

- a representative concurrent query workload;
- p50/p95 latency and queue-wait measurements;
- a real package-temperature sensor and a user-approved safe bound;
- a 30-minute thermal soak with no watcher or ingestion workload overlap;
- a documented rollback to the one-CPU profile.

Two CPUs are not the default.

## Verified baseline

On 3,817 current files and 39,758 current chunks:

- common keyword: 13 ms warm;
- semantic cache miss: 1.2–1.4 s, cache hit: 12 ms;
- hybrid cache miss: 1.2 s, cache hit: 13 ms;
- five sequential semantic misses: 0.998–1.501 s each;
- active Ollama samples: 76–88% of its one-CPU quota;
- idle Ollama: 0%, worker: 0.24%, watcher: 0.14%.

Retrieval evaluation improved keyword Hit@5/Hit@10/MRR from `0.6667` to `0.8889`. Semantic and
hybrid Hit@5/Hit@10 remained `1.0`; no-answer, stale-document exclusion, filters, and citations
remained correct.

## Consequences

Repeated interactive searches are fast without adding thermal load. The first unique semantic
query still pays real one-core model inference latency. The dashboard exposes the query cache and
last-hour search p50/p95 so a later CPU change can be evidence-based. Keeping the model loaded uses
about 1.2–1.3 GiB while idle; it does not consume measurable idle CPU.
