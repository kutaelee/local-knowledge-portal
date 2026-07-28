from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from lkp.models import ProjectArticle, ProjectArticleRevision
from lkp.project_articles import _due_reason
from lkp.settings import Settings
from lkp_indexer.generation import (
    ProjectArticleDraft,
    _normalize_project_article_payload,
)
from lkp_indexer.project_article import (
    ArticleSource,
    _batch_drafts,
    _batch_sources,
    _covered_group_indexes,
    _fallback_draft,
    _load_cached_note,
    _note_cache_key,
    _remap_draft_source_ids,
    _render_markdown,
    _source_hash,
    _store_cached_note,
    _used_source_ids,
    _validate_draft,
)
from lkp_indexer.repository_freshness import snapshot_is_stale


def _source(source_id: str, body: str, content_hash: str) -> ArticleSource:
    return ArticleSource(
        id=source_id,
        source_type="document_chunk",
        content_hash=content_hash,
        title=f"Source {source_id}",
        body=body,
        provenance={
            "relative_path": f"docs/{source_id}.md",
            "start_line": 1,
            "end_line": 4,
        },
    )


def _draft() -> ProjectArticleDraft:
    return ProjectArticleDraft.model_validate(
        {
            "title": "A coherent current project article",
            "standfirst": {
                "sentences": [
                    {"text": "The portal is read-only.", "source_ids": ["D:1:0"]}
                ]
            },
            "sections": [
                {
                    "key": "architecture",
                    "title": "Current architecture",
                    "paragraphs": [
                        {
                            "sentences": [
                                {
                                    "text": "PostgreSQL owns the durable queue.",
                                    "source_ids": ["D:1:0"],
                                },
                                {
                                    "text": "Workers renew bounded leases.",
                                    "source_ids": ["J:2"],
                                },
                            ]
                        }
                    ],
                }
            ],
        }
    )


def test_source_hash_changes_only_when_manifest_content_changes():
    original = [_source("D:1:0", "first", "hash-a")]
    same = [_source("D:1:0", "different transient body", "hash-a")]
    changed = [_source("D:1:0", "second", "hash-b")]

    assert _source_hash(original) == _source_hash(same)
    assert _source_hash(original) != _source_hash(changed)


def test_repository_snapshot_freshness_compares_content_fingerprints():
    assert snapshot_is_stale(None, "new")
    assert snapshot_is_stale("old", "new")
    assert not snapshot_is_stale("same", "same")


def test_deterministic_fallback_cites_only_available_changed_sources():
    current = _source("D:1:0", "current", "hash-current")
    unchanged = _source("D:2:0", "same", "hash-same")
    previous = ProjectArticleRevision(
        article_id=ProjectArticle().id,
        revision_number=1,
        title="Portal",
        standfirst_json={
            "sentences": [
                {"text": "Last verified overview.", "source_ids": ["D:2:0"]}
            ]
        },
        sections_json=[
            {
                "key": "overview",
                "title": "Overview",
                "paragraphs": [
                    {
                        "sentences": [
                            {
                                "text": "Last verified body.",
                                "source_ids": ["D:2:0"],
                            }
                        ]
                    }
                ],
            }
        ],
        sources_json=[],
        source_manifest_json=[
            current.catalog(),
            unchanged.catalog(),
        ],
        source_hash="old",
        provider="ollama",
        model="editor",
        model_digest="digest",
        prompt_version="prompt",
        change_summary_json={},
    )
    changed = _source("D:1:0", "updated", "hash-updated")

    draft, selected, preserved = _fallback_draft(
        "portal",
        [changed, unchanged],
        editorial_revision=previous,
        language="ko",
    )

    assert [item.id for item in selected] == ["D:1:0"]
    assert preserved is True
    assert draft.standfirst.sentences[0].text == "Last verified overview."
    assert draft.sections[0].paragraphs[0].sentences[0].text == "Last verified body."
    assert draft.sections[-1].key == "update_status"
    assert "Source D:1:0" not in str(draft.model_dump())
    assert set(_used_source_ids(draft)) == {"D:1:0", "D:2:0"}


def test_deterministic_fallback_drops_editorial_body_with_stale_citations():
    current = _source("D:1:0", "current", "hash-current")
    previous = ProjectArticleRevision(
        article_id=ProjectArticle().id,
        revision_number=2,
        title="Portal",
        standfirst_json={
            "sentences": [
                {"text": "Stale overview.", "source_ids": ["D:removed:0"]}
            ]
        },
        sections_json=[
            {
                "key": "overview",
                "title": "Overview",
                "paragraphs": [
                    {
                        "sentences": [
                            {
                                "text": "Stale body.",
                                "source_ids": ["D:removed:0"],
                            }
                        ]
                    }
                ],
            }
        ],
        sources_json=[],
        source_manifest_json=[],
        source_hash="old",
        provider="ollama",
        model="editor",
        model_digest="digest",
        prompt_version="prompt",
        change_summary_json={},
    )

    draft, selected, preserved = _fallback_draft(
        "portal",
        [current],
        editorial_revision=previous,
        language="en",
    )

    assert [item.id for item in selected] == ["D:1:0"]
    assert preserved is False
    assert "Stale overview." not in str(draft.model_dump())
    assert set(_used_source_ids(draft)) == {"D:1:0"}


def test_project_article_due_reason_covers_new_changed_and_failed_projects():
    settings = Settings(
        generation_model="editor-v1",
        generation_model_digest="sha256:editor-v1",
        project_article_prompt_version="project-article-v3-test",
    )
    now = datetime.now(timezone.utc)
    article = ProjectArticle(
        project_key="portal",
        status="current",
        last_compared_at=now,
    )
    revision = ProjectArticleRevision(
        article_id=article.id,
        revision_number=1,
        title="Portal",
        standfirst_json={},
        sections_json=[],
        sources_json=[],
        source_manifest_json=[],
        source_hash="hash",
        provider="ollama",
        model="editor-v1",
        model_digest="sha256:editor-v1",
        prompt_version="project-article-v3-test",
        change_summary_json={},
    )

    assert _due_reason(
        None,
        None,
        source_latest_at=now,
        settings=settings,
    ) == "missing"
    assert _due_reason(
        article,
        revision,
        source_latest_at=now + timedelta(seconds=1),
        settings=settings,
    ) == "source_changed"
    article.status = "error"
    assert _due_reason(
        article,
        revision,
        source_latest_at=now,
        settings=settings,
    ) == "retry"


def test_source_batching_processes_every_source_once():
    sources = [
        _source("D:1:0", "a" * 300, "a"),
        _source("D:2:0", "b" * 300, "b"),
        _source("J:3", "c" * 300, "c"),
    ]

    batches = _batch_sources(sources, max_chars=700)

    assert [item.id for batch in batches for item in batch] == [
        item.id for item in sources
    ]
    assert len(batches) > 1


def test_editorial_note_batching_processes_every_note_once():
    drafts = [_draft(), _draft(), _draft()]

    batches = _batch_drafts(drafts, max_chars=600)

    assert [item for batch in batches for item in batch] == drafts
    assert len(batches) > 1


def test_validated_editorial_note_cache_round_trips_by_revision(tmp_path):
    settings = Settings(runtime_dir=tmp_path / "runtime")
    provider = SimpleNamespace(provider="ollama", model="editor-v1")
    key = _note_cache_key(
        project="portal",
        phase="evidence_digest",
        inputs=[("D:1:0", "hash-a")],
        provider=provider,
        settings=settings,
    )
    _store_cached_note(
        settings,
        project="portal",
        key=key,
        draft=_draft(),
    )

    assert _load_cached_note(
        settings,
        project="portal",
        key=key,
        allowed_ids={"D:1:0", "J:2"},
    ) == _draft()
    assert (
        _load_cached_note(
            settings,
            project="portal",
            key=key,
            allowed_ids={"D:1:0"},
        )
        is None
    )


def test_article_fails_closed_on_unknown_or_removed_source():
    with pytest.raises(ValueError, match="unknown or removed"):
        _validate_draft(_draft(), {"D:1:0"})


def test_article_fails_closed_when_a_source_batch_disappears():
    with pytest.raises(ValueError, match="omitted an evidence batch"):
        _validate_draft(
            _draft(),
            {"D:1:0", "J:2", "D:3:0"},
            required_source_groups=[{"D:1:0"}, {"D:3:0"}],
        )


def test_editorial_coverage_counts_each_group_at_most_once():
    groups = [
        {"D:1:0"},
        {"J:2", "D:3:0"},
        {"D:4:0"},
    ]

    assert _covered_group_indexes(_draft(), groups) == {0, 1}


def test_article_fails_closed_on_list_stitched_into_sentence():
    draft = _draft()
    draft.standfirst.sentences[0].text = "요약:\n- 발췌 하나\n- 발췌 둘"

    with pytest.raises(ValueError, match="block Markdown"):
        _validate_draft(draft, {"D:1:0", "J:2"})


def test_model_source_aliases_round_trip_without_changing_provenance():
    aliases = {"D:1:0": "S0001", "J:2": "S0002"}
    aliased = _remap_draft_source_ids(_draft(), aliases)

    assert aliased is not None
    assert aliased.standfirst.sentences[0].source_ids == ["S0001"]
    assert aliased.sections[0].paragraphs[0].sentences[1].source_ids == ["S0002"]

    restored = _remap_draft_source_ids(
        aliased,
        {alias: source_id for source_id, alias in aliases.items()},
    )

    assert restored == _draft()


def test_markdown_places_source_number_after_each_sentence():
    draft = _draft()
    sources = [
        {
            **_source("D:1:0", "source", "a").catalog(include_excerpt=True),
            "citation_number": 1,
        },
        {
            **ArticleSource(
                id="J:2",
                source_type="project_journal",
                content_hash="b",
                title="Journal source",
                body="journal",
                provenance={},
            ).catalog(include_excerpt=True),
            "citation_number": 2,
        },
    ]

    markdown = _render_markdown(
        "portal",
        draft,
        sources,
        revision_number=3,
        generated_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        pipeline_version="test-v1",
    )

    assert "The portal is read-only. [1]" in markdown
    assert "PostgreSQL owns the durable queue. [1]" in markdown
    assert "Workers renew bounded leases. [2]" in markdown
    assert "revision: 3" in markdown


def test_article_normalizer_drops_empty_model_scaffolding_without_padding():
    draft = _normalize_project_article_payload(
        {
            "title": "Current article",
            "standfirst": {
                "sentences": [
                    {"text": "Supported summary.", "source_ids": ["D:1:0"]}
                ]
            },
            "sections": [
                {
                    "key": "empty",
                    "title": "Empty model section",
                    "paragraphs": [{"sentences": []}],
                },
                {
                    "key": "current",
                    "title": "Current state",
                    "paragraphs": [
                        {
                            "sentences": [
                                {
                                    "text": "Supported detail.",
                                    "source_ids": ["D:1:0"],
                                }
                            ]
                        }
                    ],
                },
            ],
        }
    )

    assert [section.key for section in draft.sections] == ["current"]
