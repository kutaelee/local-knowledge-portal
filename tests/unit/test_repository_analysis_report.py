from lkp_indexer.repository_analysis_report import render_markdown


def test_report_renders_actual_knowledge_and_auditable_intervention_details():
    project = {
        "display_name": "esb",
        "category": "IndigoESB esb",
        "snapshot_id": "snapshot-1",
        "source_hash": "source-hash",
        "model": "qwen3.6-27b-mtp-q4-k-m",
        "model_quantization": "Q4_K_M",
            "metrics": {},
            "counts": {
                "files": 1,
                "qwen_analyzed_files": 1,
                "claims": 1,
            "verified_claims": 1,
            "knowledge_items": 1,
            "searchable_knowledge": 1,
            "embeddings": 1,
        },
        "knowledge": [
            {
                "knowledge_type": "CALL_FLOW",
                "title": "요청 처리 흐름",
                "summary": "진입점에서 처리기로 요청을 전달한다.",
                "processing_steps": ["진입", "처리"],
                "components": ["Gateway"],
                "configurations": ["gateway.xml"],
                "dependencies": ["indigo-core.jar"],
                "source_references": [
                    {
                        "file": "src/Gateway.java",
                        "start_line": 10,
                        "end_line": 20,
                        "symbol": "handle",
                    }
                ],
                "unknowns": [],
                "searchable": True,
            }
        ],
        "verified_claims": [
            {
                "claim_type": "CALL_FLOW",
                "component": "Gateway",
                "claim_text": "요청을 처리기로 전달한다.",
                "evidence": [
                    {
                        "file": "src/Gateway.java",
                        "start_line": 10,
                        "end_line": 20,
                        "symbol": "handle",
                    }
                ],
                "related_configs": ["gateway.xml"],
                "assumptions": [],
                "counter_evidence": [],
                "unknowns": [],
            }
        ],
        "claim_statuses": [
            {
                "validation_status": "SOURCE_VERIFIED",
                "claim_type": "CALL_FLOW",
                "count": 1,
            }
        ],
        "task_statuses": [
            {
                "status": "ADDITIONAL_ANALYSIS_REQUIRED",
                "failure_code": "INSUFFICIENT_EVIDENCE",
                "count": 1,
                "attempts": 2,
            }
        ],
        "task_details": [
            {
                "task_type": "COMPONENT",
                "analysis_unit": "component:batch:2",
                "status": "ADDITIONAL_ANALYSIS_REQUIRED",
                "attempt_count": 2,
                "failure_code": "INSUFFICIENT_EVIDENCE",
                "codex_intervened": True,
                "codex_claims_authored": 1,
                "codex_source_scope": ["src/Gateway.java"],
                "source_files": ["src/Gateway.java"],
                "missing_knowledge": ["운영 로그"],
                "contradictions": [],
                "failure_history": [
                    {
                        "attempt": 1,
                        "failure_code": "TimeoutError",
                        "requested_source_char_budget": 30000,
                    }
                ],
                "retry_history": [
                    {
                        "attempt": 2,
                        "strategy": "REDUCED_SOURCE_BUDGET",
                        "requested_source_char_budget": 15000,
                    }
                ],
                "additional_evidence_requests": ["운영 로그"],
                "started_at": None,
                "finished_at": None,
            }
        ],
        "evaluation_categories": [
            {
                "question_type": "CALL_FLOW",
                "result_count": 1,
                "passed": 1,
                "scopes": ["post_persistence_answer_quality"],
            }
        ],
        "evaluation_scopes": [
            {
                "scope": "post_persistence_answer_quality",
                "count": 1,
                "passed": 1,
                "average_score": 1.0,
            }
        ],
        "evaluation_failures": [],
    }
    report = {
        "generated_at": "2026-07-27T00:00:00+00:00",
        "projects": [project],
        "invariants": {
            "invalid_claim_refs": 0,
            "invalid_knowledge_refs": 0,
        },
        "completeness": {
            "required_projects": [
                "IndigoESB esb",
                "IndigoESB imc",
                "IndigoESB agent",
            ],
            "missing_projects": ["IndigoESB imc", "IndigoESB agent"],
            "required_support_categories": ["CALL_FLOW"],
            "missing_support_categories": {"IndigoESB esb": []},
        },
    }

    rendered = render_markdown(report)

    assert "진입점에서 처리기로 요청을 전달한다." in rendered
    assert "src/Gateway.java:10-20 (handle)" in rendered
    assert "component:batch:2" in rendered
    assert "Codex가 대신 작성하거나 수정한 Claim" in rendered
    assert "- esb: 1" in rendered
    assert "CALL_FLOW" in rendered
    assert "post_persistence_answer_quality" in rendered
    assert "REDUCED_SOURCE_BUDGET" in rendered
    assert "TimeoutError" in rendered
