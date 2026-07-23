import json
from dataclasses import dataclass

from lkp.db import SessionLocal
from lkp.schemas import SearchRequest
from lkp.search import search
from lkp.settings import get_settings


@dataclass(frozen=True)
class Case:
    kind: str
    query: str
    should_answer: bool


CASES = [
    Case("exact filename", "README.md", True),
    Case("partial path", "README", True),
    Case("Korean natural language", "로컬 지식베이스", True),
    Case("code symbol", '"FOR UPDATE SKIP LOCKED"', True),
    Case("error phrase", '"worker lease expires"', True),
    Case("work reason", '"separate broker" unnecessary', True),
    Case("ADR decision", '"PostgreSQL row locking"', True),
    Case("similar expression", "database queue without message broker", True),
    Case("change time", "지난주에 변경한 문서", False),
    Case("nonexistent information", "Mars telemetry retention policy", False),
]


def main() -> None:
    settings = get_settings()
    positive = [case for case in CASES if case.should_answer]
    hits5 = hits10 = 0
    reciprocal_ranks: list[float] = []
    negative_correct = 0
    citations = citations_correct = 0
    details = []
    with SessionLocal() as session:
        for case in CASES:
            response = search(
                session,
                SearchRequest(query=case.query, mode="keyword", top_k=10),
                settings,
            )
            ranks = [
                index
                for index, result in enumerate(response.results, 1)
                if result.provenance.relative_path == "README.md"
            ]
            first_rank = ranks[0] if ranks else None
            if case.should_answer:
                hits5 += int(first_rank is not None and first_rank <= 5)
                hits10 += int(first_rank is not None and first_rank <= 10)
                reciprocal_ranks.append(1 / first_rank if first_rank else 0)
            else:
                negative_correct += int(not response.results)
            for result in response.results:
                citations += 1
                provenance = result.provenance
                citations_correct += int(
                    provenance.start_line > 0
                    and provenance.end_line >= provenance.start_line
                    and len(provenance.content_hash) == 64
                    and bool(provenance.canonical_path)
                )
            details.append(
                {
                    "kind": case.kind,
                    "query": case.query,
                    "expected_answer": case.should_answer,
                    "result_count": len(response.results),
                    "first_relevant_rank": first_rank,
                }
            )
        session.commit()
    print(
        json.dumps(
            {
                "corpus_documents": 1,
                "mode": "keyword baseline",
                "hit_rate_at_5": hits5 / len(positive),
                "hit_rate_at_10": hits10 / len(positive),
                "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks),
                "no_answer_correctness": negative_correct / (len(CASES) - len(positive)),
                "citation_correctness": citations_correct / citations if citations else 1,
                "cases": details,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
