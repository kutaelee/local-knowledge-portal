import json
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from lkp.db import get_db
from lkp.main import app
from lkp.models import IngestJob, SourceRoot
from lkp_indexer.repository_analysis.checkpoint import (
    RepositoryAnalysisCheckpointStore,
)
from lkp_indexer.repository_analysis.domain import (
    Claim,
    Confidence,
    EvidenceReference,
    utcnow,
)
from lkp_indexer.repository_analysis.jobs import process_repository_analysis_job
from lkp_indexer.repository_analysis.pipeline import RepositoryAnalysisPipeline
from lkp_indexer.repository_analysis.repository import RepositoryAnalysisStore
from lkp_indexer.repository_analysis_report import collect, render_markdown
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url():
    value = os.getenv("LKP_TEST_DATABASE_URL")
    if not value:
        pytest.skip("LKP_TEST_DATABASE_URL is not set")
    return value


def test_repository_analysis_snapshot_and_api(
    database_url: str,
    tmp_path: Path,
) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    source = tmp_path / "support-service"
    source.mkdir()
    (source / "service.py").write_text(
        """
class SupportService:
    def diagnose(self, incident):
        return incident.get("status")
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (source / "application.yaml").write_text(
        "support:\n  timeout: 15\n",
        encoding="utf-8",
    )
    (source / "repository.py").write_text(
        "def persist(result):\n    return bool(result)\n",
        encoding="utf-8",
    )
    (source / ".env").write_text("PASSWORD=must-not-persist\n", encoding="utf-8")
    manifest = RepositoryAnalysisPipeline(allowed_roots=[source]).run(source)

    with Session(engine) as session:
        store = RepositoryAnalysisStore(session)
        assert store.persist(manifest, category="IndigoESB esb") is True
        assert store.persist(manifest, category="IndigoESB esb") is False
        overview = next(
            item
            for item in manifest.knowledge_items
            if item.knowledge_type == "REPOSITORY_OVERVIEW"
        )
        overview.summary = "새 분석 계약으로 다시 생성한 저장소 전체 설명입니다."
        manifest.metrics["repository_report_contract"] = "human-readable-v-next"
        assert store.persist(manifest, category="IndigoESB esb") is True
        assert store.persist(manifest, category="IndigoESB esb") is False
        overview.summary = "모델 근거 종합으로 승격한 저장소 전체 설명입니다."
        manifest.metrics["repository_report_mode"] = "MODEL_EVIDENCE_SYNTHESIS"
        manifest.metrics["repository_report_quality_gate"] = "EVIDENCE_SYNTHESIZED"
        assert store.persist(manifest, category="IndigoESB esb") is True
        assert store.persist(manifest, category="IndigoESB esb") is False
        assert (
            session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM repository_knowledge_item
                    WHERE snapshot_id = :snapshot_id
                      AND knowledge_type = 'REPOSITORY_OVERVIEW'
                    """
                ),
                {"snapshot_id": manifest.snapshot_id},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    """
                    SELECT summary
                    FROM repository_knowledge_item
                    WHERE snapshot_id = :snapshot_id
                      AND knowledge_type = 'REPOSITORY_OVERVIEW'
                    """
                ),
                {"snapshot_id": manifest.snapshot_id},
            ).scalar_one()
            == overview.summary
        )
        source_columns = {
            item
            for item in session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'repository_source_file'
                    """
                )
            ).scalars()
        }
        assert "content" not in source_columns
        assert "source_text" not in source_columns
        assert (
            session.execute(
                text(
                    """
                SELECT count(*) FROM repository_snapshot
                WHERE project_id = :project_id AND source_hash = :source_hash
                """
                ),
                {
                    "project_id": manifest.project_id,
                    "source_hash": manifest.source_hash,
                },
            ).scalar_one()
            == 1
        )
        report = collect(lambda: Session(engine))
        assert len(report["projects"]) == 1
        assert report["projects"][0]["category"] == "IndigoESB esb"
        assert len(report["projects"][0]["evaluation_categories"]) == 15
        assert report["projects"][0]["knowledge"]
        assert report["completeness"]["missing_projects"] == [
            "IndigoESB agent",
            "IndigoESB imc",
        ]
        assert len(report["completeness"]["missing_support_categories"]["IndigoESB esb"]) == 15
        assert all(
            scopes
            == [
                "post_persistence_hybrid_retrieval",
                "post_persistence_support_answer",
            ]
            for scopes in report["completeness"]["missing_support_scopes"]["IndigoESB esb"].values()
        )
        assert report["invariants"]["raw_source_columns"] == 0
        assert report["invariants"]["forbidden_source_payloads"] == 0
        rendered_report = render_markdown(report)
        assert "등록된 지식 내용" in rendered_report
        assert "ENTRY_POINT" in rendered_report
        source_root = SourceRoot(
            name="repository-analysis-test",
            canonical_path=str(tmp_path),
            source_type="local_filesystem",
            read_only=True,
            enabled=True,
            include_patterns=[],
            exclude_patterns=[],
        )
        session.add(source_root)
        session.commit()

        def override_db():
            yield session

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        try:
            projects = client.get("/api/v1/repository-analysis/projects")
            assert projects.status_code == 200
            assert any(item["id"] == str(manifest.project_id) for item in projects.json()["items"])
            detail = client.get(f"/api/v1/repository-analysis/projects/{manifest.project_id}")
            assert detail.status_code == 200
            assert detail.json()["counts"]["symbols"] >= 2
            assert detail.json()["counts"]["components"] >= 1
            assert detail.json()["counts"]["lifecycle_nodes"] >= 1
            assert detail.json()["report"]["knowledge_type"] == "REPOSITORY_OVERVIEW"
            assert detail.json()["report"]["summary"]
            assert detail.json()["report"]["source_references"]
            assert detail.json()["visualization"]["components"] == []
            assert detail.json()["visualization"]["lifecycle"]["nodes"] == []
            assert detail.json()["visualization"]["relation_groups"]
            assert all(
                set(item) == {"key", "count"}
                for item in detail.json()["visualization"]["relation_groups"]
            )
            architecture = client.get(
                f"/api/v1/repository-analysis/projects/{manifest.project_id}/visualization",
                params={"section": "architecture"},
            )
            assert architecture.status_code == 200
            assert architecture.json()["components"]
            logic = client.get(
                f"/api/v1/repository-analysis/projects/{manifest.project_id}/visualization",
                params={"section": "logic"},
            )
            assert logic.status_code == 200
            assert logic.json()["lifecycle"]["nodes"]
            assert logic.json()["lifecycle"]["edges"]
            dependencies = client.get(
                f"/api/v1/repository-analysis/projects/{manifest.project_id}/visualization",
                params={"section": "dependencies"},
            )
            assert dependencies.status_code == 200
            assert "dependencies" in dependencies.json()
            knowledge = client.get(
                f"/api/v1/repository-analysis/projects/{manifest.project_id}/knowledge"
            )
            assert knowledge.status_code == 200
            assert knowledge.json()["items"]
            configurations = client.get(
                f"/api/v1/repository-analysis/projects/{manifest.project_id}/configurations"
            )
            assert configurations.status_code == 200
            assert configurations.json()["items"]
            search = client.get(
                "/api/v1/repository-analysis/search",
                params={
                    "q": "구성",
                    "project_id": str(manifest.project_id),
                    "snapshot_id": str(manifest.snapshot_id),
                    "mode": "keyword",
                },
            )
            assert search.status_code == 200
            assert search.json()["items"]
            symbol_search = client.get(
                "/api/v1/repository-analysis/search",
                params={
                    "q": "Where is SupportService declared?",
                    "project_id": str(manifest.project_id),
                    "snapshot_id": str(manifest.snapshot_id),
                    "mode": "keyword",
                },
            )
            assert symbol_search.status_code == 200
            assert symbol_search.json()["items"]
            assert any(
                reference.get("symbol") == "SupportService"
                for reference in symbol_search.json()["items"][0]["source_references"]
            )
            unscoped_search = client.get(
                "/api/v1/repository-analysis/search",
                params={"q": "구성"},
            )
            assert unscoped_search.status_code == 422
            request = client.post(
                "/api/v1/repository-analysis/analyze",
                json={"source_path": str(source)},
            )
            assert request.status_code == 202
            request_payload = request.json()
            queued = client.get(
                f"/api/v1/repository-analysis/analysis-requests/{request_payload['job_id']}"
            )
            assert queued.status_code == 200
            assert queued.json()["status"] == "pending"
            session.expire_all()
            queued_job = session.get(
                IngestJob,
                uuid.UUID(request_payload["job_id"]),
            )
            assert queued_job is not None
            process_repository_analysis_job(
                session,
                queued_job,
                "repository-analysis-integration-test",
            )
            session.commit()
            assert queued_job.status.value == "succeeded"
            assert queued_job.error_details["project_id"] == str(manifest.project_id)
            assert queued_job.error_details["source_text_stored"] is False
            assert queued_job.error_details["local_model_used"] is False
            rejected = client.post(
                "/api/v1/repository-analysis/analyze",
                json={"source_path": "/not/allowlisted/repository"},
            )
            assert rejected.status_code == 422
            tool = client.get(
                "/api/v1/repository-analysis/tools/get_project_architecture",
                params={"project_id": str(manifest.project_id)},
            )
            assert tool.status_code == 200
            assert tool.json()["grounding_required"] is True
            assert tool.json()["context"]
            original_references = [
                dict(row)
                for row in session.execute(
                    text(
                        """
                        SELECT id, source_references
                        FROM repository_knowledge_item
                        WHERE snapshot_id = :snapshot_id
                        """
                    ),
                    {"snapshot_id": manifest.snapshot_id},
                ).mappings()
            ]
            session.execute(
                text(
                    """
                    UPDATE repository_knowledge_item
                    SET source_references = jsonb_set(
                      source_references,
                      '{0,source_hash}',
                      to_jsonb(repeat('0', 64)),
                      false
                    )
                    WHERE snapshot_id = :snapshot_id
                    """
                ),
                {"snapshot_id": manifest.snapshot_id},
            )
            session.commit()
            try:
                invalid_detail = client.get(
                    f"/api/v1/repository-analysis/projects/{manifest.project_id}"
                )
                invalid_knowledge = client.get(
                    f"/api/v1/repository-analysis/projects/{manifest.project_id}/knowledge"
                )
                invalid_tool = client.get(
                    "/api/v1/repository-analysis/tools/get_project_architecture",
                    params={"project_id": str(manifest.project_id)},
                )
                invalid_search = client.get(
                    "/api/v1/repository-analysis/search",
                    params={
                        "q": "architecture lifecycle overview",
                        "project_id": str(manifest.project_id),
                        "snapshot_id": str(manifest.snapshot_id),
                        "mode": "keyword",
                    },
                )
                assert invalid_detail.status_code == 200
                assert invalid_detail.json()["report"] is None
                assert invalid_knowledge.status_code == 200
                assert invalid_knowledge.json()["total"] == 0
                assert invalid_tool.status_code == 200
                assert invalid_tool.json()["context"] == []
                assert invalid_search.status_code == 200
                assert invalid_search.json()["items"] == []
            finally:
                for row in original_references:
                    session.execute(
                        text(
                            """
                            UPDATE repository_knowledge_item
                            SET source_references = CAST(:source_references AS jsonb)
                            WHERE id = :id
                            """
                        ),
                        {
                            "id": row["id"],
                            "source_references": json.dumps(row["source_references"]),
                        },
                    )
                session.commit()
            evaluations = client.get(
                f"/api/v1/repository-analysis/projects/{manifest.project_id}/evaluations"
            )
            assert evaluations.status_code == 200
            assert evaluations.json()["total"] == 15
            assert evaluations.json()["answer_quality_evaluated"] is False
            assert (
                sum(item["scenario_type"] is not None for item in evaluations.json()["items"]) == 15
            )
            analysis_job_id = session.execute(
                text(
                    """
                    SELECT j.id
                    FROM repository_analysis_job j
                    WHERE j.snapshot_id=:snapshot_id
                    ORDER BY j.started_at DESC
                    LIMIT 1
                    """
                ),
                {"snapshot_id": manifest.snapshot_id},
            ).scalar_one()
            session.execute(
                text(
                    """
                    UPDATE repository_analysis_job
                    SET metrics=jsonb_set(
                      metrics,
                      '{source_excerpt}',
                      to_jsonb(CAST('sentinel-only' AS text))
                    )
                    WHERE id=:analysis_job_id
                    """
                ),
                {"analysis_job_id": analysis_job_id},
            )
            session.commit()
            contaminated = collect(lambda: Session(engine))
            assert contaminated["invariants"]["forbidden_source_payloads"] == 1
            session.execute(
                text(
                    """
                    UPDATE repository_analysis_job
                    SET metrics=metrics - 'source_excerpt'
                    WHERE id=:analysis_job_id
                    """
                ),
                {"analysis_job_id": analysis_job_id},
            )
            session.commit()
            manifest.model = "test/new-analysis-model"
            manifest.model_quantization = "NVFP4"
            manifest.prompt_version = "repository-analysis-test-v2"
            manifest.correlation_id = uuid.uuid4()
            assert store.persist(manifest, category="IndigoESB esb") is True
            assert store.persist(manifest, category="IndigoESB esb") is False
            assert (
                session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM repository_snapshot
                        WHERE project_id = :project_id
                          AND source_hash = :source_hash
                        """
                    ),
                    {
                        "project_id": manifest.project_id,
                        "source_hash": manifest.source_hash,
                    },
                ).scalar_one()
                == 1
            )
            assert (
                session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM repository_analysis_job
                        WHERE snapshot_id = :snapshot_id
                        """
                    ),
                    {"snapshot_id": manifest.snapshot_id},
                ).scalar_one()
                == 4
            )
            latest_evaluations = client.get(
                f"/api/v1/repository-analysis/projects/{manifest.project_id}/evaluations"
            )
            assert latest_evaluations.status_code == 200
            assert latest_evaluations.json()["total"] == 15
        finally:
            app.dependency_overrides.clear()


def test_repository_analysis_task_checkpoint_round_trip(
    database_url: str,
    tmp_path: Path,
) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    source = tmp_path / "checkpoint-service"
    source.mkdir()
    for index in range(6):
        (source / f"service_{index}.py").write_text(
            f"def service_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    pipeline = RepositoryAnalysisPipeline(allowed_roots=[source])
    manifest = pipeline.run(source)
    tasks = pipeline._analysis_tasks(manifest)
    task = tasks[0]
    file = next(item for item in manifest.files if item.relative_path == task["files"][0])
    claim = Claim(
        claim="The checkpoint preserves a verified candidate fact.",
        claim_type="CODE_FACT",
        component=task["key"],
        evidence=[
            EvidenceReference(
                file=file.relative_path,
                source_hash=file.content_hash,
                start_line=1,
                end_line=1,
            )
        ],
        confidence=Confidence.HIGH,
    )
    finished_at = utcnow()
    outcome = {
        "task_id": task["task_id"],
        "key": task["key"],
        "source_files": sorted(task["files"]),
        "attempts": 1,
        "status": "SOURCE_EXTRACTED",
        "failure_code": None,
        "claims_accepted": 1,
        "claims_rejected": 0,
        "model_requests": 1,
        "model_latency_ms": 10,
        "model_prompt_tokens": 100,
        "model_completion_tokens": 20,
        "request_failures_exhausted": False,
        "started_at": finished_at,
        "finished_at": finished_at,
    }

    with Session(engine) as session:
        store = RepositoryAnalysisCheckpointStore(session)
        checkpoint = store.begin(
            manifest,
            fingerprint="a" * 64,
            planned_task_count=len(tasks),
        )
        store.save_task(checkpoint, outcome, [claim])

    with Session(engine) as session:
        store = RepositoryAnalysisCheckpointStore(session)
        restored = store.restore(checkpoint, tasks)
        restored_outcome, restored_claims = restored[str(task["task_id"])]
        assert restored_outcome["status"] == "SOURCE_EXTRACTED"
        assert restored_outcome["finished_at"] == finished_at
        assert restored_claims[0].claim == claim.claim
        assert restored_claims[0].evidence == claim.evidence
        outcome["request_failures_exhausted"] = True
        store.save_task(checkpoint, outcome, [claim])
        assert store.restore(checkpoint, tasks, retry_exhausted=True) == {}
        assert (
            session.execute(
                text(
                    """
                SELECT count(*)
                FROM repository_analysis_task_checkpoint
                WHERE checkpoint_id = :checkpoint_id
                """
                ),
                {"checkpoint_id": checkpoint},
            ).scalar_one()
            == 1
        )
        checkpoint_columns = set(
            session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name IN (
                      'repository_analysis_checkpoint',
                      'repository_analysis_task_checkpoint'
                    )
                    """
                )
            ).scalars()
        )
        assert "source_text" not in checkpoint_columns
        assert "prompt" not in checkpoint_columns
        assert "raw_response" not in checkpoint_columns


def test_repository_snapshot_reactivation_is_single_and_latest(
    database_url: str,
    tmp_path: Path,
) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    source = tmp_path / "revision-reuse-service"
    source.mkdir()
    target = source / "service.py"
    original = "def current_revision():\n    return 'v1'\n"
    target.write_text(original, encoding="utf-8")
    pipeline = RepositoryAnalysisPipeline(allowed_roots=[source])
    first = pipeline.run(source)

    with Session(engine) as session:
        store = RepositoryAnalysisStore(session)
        assert store.persist(first, category="revision lifecycle test") is True
        first_snapshot_id = first.snapshot_id

        target.write_text("def current_revision():\n    return 'v2'\n", encoding="utf-8")
        second = pipeline.run(source)
        assert second.source_hash != first.source_hash
        assert store.persist(second, category="revision lifecycle test") is True

        target.write_text(original, encoding="utf-8")
        reverted = pipeline.run(source)
        assert reverted.source_hash == first.source_hash
        # The original analysis is reused, but its snapshot must again become
        # the one deterministic current/latest revision.
        assert store.persist(reverted, category="revision lifecycle test") is False
        rows = (
            session.execute(
                text(
                    """
                SELECT id, stale, created_at
                FROM repository_snapshot
                WHERE project_id = :project_id
                ORDER BY created_at DESC, id DESC
                """
                ),
                {"project_id": first.project_id},
            )
            .mappings()
            .all()
        )
        assert rows[0]["id"] == first_snapshot_id
        assert rows[0]["stale"] is False
        assert sum(row["stale"] is False for row in rows) == 1
