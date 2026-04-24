"""Tests for the fast report domain service."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from shared.fast_report_types import (
    FastReportCitation,
    FastReportDiagram,
    FastReportEvidenceBlock,
    FastReportWikiLink,
)


def test_arbitrate_report_claims_drops_unsupported_claims():
    from worker.fast_report import FastReportClaim, arbitrate_report_claims

    claims = [
        FastReportClaim(
            text="Indexing starts in worker/jobs.py.",
            citation_ids=["code-1"],
            supporting_layers=["code_evidence"],
        ),
        FastReportClaim(
            text="The system uses Celery workers.",
            citation_ids=["sem-1"],
            supporting_layers=["semantic_retrieval"],
        ),
        FastReportClaim(
            text="The repo is split across api, worker, and web.",
            citation_ids=["struct-1"],
            supporting_layers=["repository_structure"],
        ),
    ]

    supported = arbitrate_report_claims(claims)

    assert [claim.text for claim in supported] == [
        "Indexing starts in worker/jobs.py.",
        "The repo is split across api, worker, and web.",
    ]


def test_assemble_fast_report_markdown_uses_canonical_heading_order():
    from worker.fast_report import FastReportClaim, assemble_fast_report_markdown

    markdown = assemble_fast_report_markdown(
        title="Indexing Flow",
        summary="How the indexing pipeline is orchestrated.",
        section_claims={
            "Use Cases": [
                FastReportClaim(
                    text="Reindexing is used after repository changes.",
                    citation_ids=["code-2"],
                    supporting_layers=["code_evidence"],
                )
            ],
            "Overview": [
                FastReportClaim(
                    text="Indexing is coordinated by worker jobs.",
                    citation_ids=["code-1"],
                    supporting_layers=["code_evidence"],
                )
            ],
            "Further Explore": [],
        },
        notes=["Progress and persistence are tracked through job records."],
        related_wiki_pages=[
            FastReportWikiLink(slug="overview", title="Overview", reason="System map")
        ],
        related_diagrams=[
            FastReportDiagram(
                id="diagram-1",
                title="Pipeline Flow",
                type="flowchart",
                source="wiki",
                reason="Visual call path",
            )
        ],
    )

    overview_index = markdown.index("## Overview")
    use_cases_index = markdown.index("## Use Cases")
    notes_index = markdown.index("## Notes")
    further_explore_index = markdown.index("## Further Explore")

    assert overview_index < use_cases_index < notes_index < further_explore_index
    assert "Indexing is coordinated by worker jobs. [code-1]" in markdown
    assert "Reindexing is used after repository changes. [code-2]" in markdown
    assert "- Wiki: [Overview](wiki://overview) - System map" in markdown
    assert "- Diagram: Pipeline Flow (`flowchart`) - Visual call path" in markdown


def test_parse_draft_sections_normalizes_heading_variants():
    from worker.fast_report import _parse_draft_sections

    section_claims = _parse_draft_sections(
        [
            {
                "heading": "Implementation Details",
                "claims": [
                    {
                        "text": "Key details live in worker/jobs.py.",
                        "citation_ids": ["code-1"],
                        "supporting_layers": ["code_evidence"],
                    }
                ],
            },
            {
                "heading": "Execution Flow",
                "claims": [
                    {
                        "text": "The job clones before later stages run.",
                        "citation_ids": ["struct-1", "code-1"],
                        "supporting_layers": [
                            "repository_structure",
                            "code_evidence",
                        ],
                    }
                ],
            },
        ]
    )

    assert list(section_claims) == [
        "Key Implementation Details",
        "Execution Flow / Steps",
    ]


async def test_generate_fast_report_section_returns_structured_section(mock_llm):
    from worker.fast_report import (
        CodeEvidenceLayer,
        CuratedKnowledgeLayer,
        FastReportQuestionIntent,
        RepositoryStructureLayer,
        SemanticRetrievalLayer,
        generate_fast_report_section,
    )

    async def _structured(*args, **kwargs):
        prompt = args[0]
        if "Classify the user's repository question" in prompt:
            return {
                "question_type": "execution_flow",
                "target": "indexing pipeline",
                "answer_shape": "report",
                "evidence_shape": "entry-points",
            }
        return {
            "title": "Indexing Flow",
            "summary": "The indexing pipeline starts in the worker job orchestration.",
            "sections": [
                {
                    "heading": "Overview",
                    "claims": [
                        {
                            "text": "Indexing starts in worker/jobs.py.",
                            "citation_ids": ["code-1"],
                            "supporting_layers": ["code_evidence"],
                        },
                        {
                            "text": "The system uses Celery queues.",
                            "citation_ids": ["sem-1"],
                            "supporting_layers": ["semantic_retrieval"],
                        },
                    ],
                },
                {
                    "heading": "Execution Flow",
                    "claims": [
                        {
                            "text": (
                                "The repository is cloned before AST and RAG "
                                "stages run."
                            ),
                            "citation_ids": ["struct-1", "code-1"],
                            "supporting_layers": [
                                "repository_structure",
                                "code_evidence",
                            ],
                        }
                    ],
                },
            ],
            "notes": ["Deep research is a separate orchestration path."],
        }

    mock_llm.generate_structured.side_effect = _structured

    async def _repo_structure(question: str, intent: FastReportQuestionIntent):
        assert question == "How does indexing work?"
        assert intent.question_type == "execution_flow"
        return RepositoryStructureLayer(
            signals=["worker -> pipeline stages"],
            citations=[
                FastReportCitation(
                    id="struct-1",
                    file_path="worker/jobs.py",
                    start_line=539,
                    end_line=566,
                    label="Pipeline stages",
                    kind="repository_structure",
                )
            ],
        )

    async def _code_evidence(question: str, intent: FastReportQuestionIntent):
        return CodeEvidenceLayer(
            snippets=[
                {
                    "file": "worker/jobs.py",
                    "start_line": 539,
                    "end_line": 566,
                    "text": "async def run_full_index(...): ...",
                }
            ],
            citations=[
                FastReportCitation(
                    id="code-1",
                    file_path="worker/jobs.py",
                    start_line=539,
                    end_line=566,
                    label="run_full_index",
                    kind="code_evidence",
                )
            ],
            evidence_blocks=[
                FastReportEvidenceBlock(
                    citation_id="code-1",
                    snippet_start=539,
                    snippet_end=566,
                    full_start=536,
                    full_end=584,
                    code="async def run_full_index(...): ...",
                    symbol_path="worker.jobs.run_full_index",
                ),
                FastReportEvidenceBlock(
                    citation_id="code-2",
                    snippet_start=10,
                    snippet_end=40,
                    full_start=1,
                    full_end=60,
                    code="def clone_or_fetch(...): ...",
                    symbol_path="worker.ingestion.clone_or_fetch",
                ),
            ],
        )

    async def _semantic(question: str, intent: FastReportQuestionIntent):
        return SemanticRetrievalLayer(
            passages=["The README describes a six-stage pipeline."],
            citations=[
                FastReportCitation(
                    id="code-2",
                    file_path="worker/ingestion.py",
                    start_line=10,
                    end_line=40,
                    label="clone_or_fetch",
                    kind="code_evidence",
                ),
                FastReportCitation(
                    id="sem-1",
                    file_path="README.md",
                    start_line=1,
                    end_line=20,
                    label="README pipeline summary",
                    kind="semantic_retrieval",
                ),
            ],
        )

    async def _curated(question: str, intent: FastReportQuestionIntent):
        return CuratedKnowledgeLayer(
            summaries=["Overview wiki page summarises the system architecture."],
            wiki_pages=[
                FastReportWikiLink(
                    slug="overview",
                    title="Overview",
                    reason="High-level architecture page",
                )
            ],
            diagrams=[
                FastReportDiagram(
                    id="diagram-1",
                    title="Pipeline Flow",
                    type="flowchart",
                    source="wiki",
                    reason="Generated system flow diagram",
                )
            ],
        )

    result = await generate_fast_report_section(
        question="How does indexing work?",
        repo_name="autowiki",
        llm=mock_llm,
        repository_structure_retriever=_repo_structure,
        code_evidence_retriever=_code_evidence,
        semantic_retriever=_semantic,
        curated_knowledge_retriever=_curated,
    )

    assert result.title == "Indexing Flow"
    assert result.summary.startswith("The indexing pipeline starts")
    assert "## Overview" in result.markdown
    assert "## Execution Flow / Steps" in result.markdown
    assert "## Further Explore" in result.markdown
    assert "The system uses Celery queues." not in result.markdown
    assert [citation.id for citation in result.citations] == ["code-1", "struct-1"]
    assert "code-2" not in result.markdown
    assert "sem-1" not in result.markdown
    assert [block.citation_id for block in result.evidence_blocks] == ["code-1"]
    assert [page.slug for page in result.related_wiki_pages] == ["overview"]
    assert [diagram.id for diagram in result.related_diagrams] == ["diagram-1"]


async def test_enqueue_fast_report_enqueues_expected_payload():
    from api.queue import enqueue_fast_report

    with patch("api.queue._enqueue") as enqueue_mock:
        job_id = await enqueue_fast_report(
            repo_id="repo-1",
            job_id="job-1",
            report_id="report-1",
            section_id="section-1",
            question="How does indexing work?",
        )

    assert job_id == "job-1"
    enqueue_mock.assert_awaited_once_with(
        "run_fast_report",
        repo_id="repo-1",
        job_id="job-1",
        report_id="report-1",
        section_id="section-1",
        question="How does indexing work?",
    )


async def test_worker_startup_registers_fast_report_runtime():
    from worker.main import startup

    ctx = {}

    await startup(ctx)

    assert callable(ctx["fast_report_retriever_factory"])


async def test_run_fast_report_persists_completed_section(
    tmp_path, monkeypatch, mock_llm, mock_embedding
):
    from shared.database import dispose_db, get_session, init_db
    from shared.models import FastReport, FastReportSection, Job, Repository, WikiPage
    from worker.jobs import run_fast_report
    from worker.main import startup

    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    from shared.config import reset_config

    reset_config()
    await init_db(db_path)
    expires_at = datetime.now(UTC) + timedelta(days=7)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="ready"))
        s.add(Job(id="j1", repo_id="r1", type="fast_report", status="queued"))
        s.add(
            FastReport(
                id="fr1",
                repo_id="r1",
                commit_sha="deadbeef",
                status="queued",
                expires_at=expires_at,
            )
        )
        s.add(
            FastReportSection(
                id="sec1",
                report_id="fr1",
                query="How does indexing work?",
                title="Pending",
                summary=None,
                markdown="",
                citations_json="[]",
                evidence_blocks_json="[]",
                related_wiki_pages_json="[]",
                related_diagrams_json="[]",
                status="queued",
            )
        )
        s.add(
            WikiPage(
                id="wp1",
                repo_id="r1",
                slug="overview",
                title="Overview",
                content="Architecture summary for the indexing pipeline.",
                description="System overview",
                page_order=0,
            )
        )
        await s.commit()

    repo_root = tmp_path / "repos" / "r1" / "clone"
    repo_root.mkdir(parents=True)
    (repo_root / "README.md").write_text("# AutoWiki\n\nIndexing overview.")

    async def _structured(*args, **kwargs):
        prompt = args[0]
        if "Classify the user's repository question" in prompt:
            return {
                "question_type": "execution_flow",
                "target": "indexing",
                "answer_shape": "report",
                "evidence_shape": "entry-points",
            }
        return {
            "title": "Indexing Flow",
            "summary": "The worker job orchestrates indexing.",
            "sections": [
                {
                    "heading": "Overview",
                    "claims": [
                        {
                            "text": "Indexing starts in worker/jobs.py.",
                            "citation_ids": ["code-1"],
                            "supporting_layers": ["code_evidence"],
                        }
                    ],
                }
            ],
            "notes": ["Uses the queued fast report background flow."],
        }

    mock_llm.generate_structured.side_effect = _structured

    fake_store = MagicMock()

    search_calls: list[dict[str, int | None]] = []

    def _search(query_vec, k=5, doc_k=None):
        search_calls.append({"k": k, "doc_k": doc_k})
        if doc_k == 3:
            return [
                {
                    "file": "README.md",
                    "text": "The README describes the indexing pipeline.",
                    "start_line": 1,
                    "end_line": 3,
                    "score": 0.61,
                }
            ]
        return [
            {
                "file": "README.md",
                "text": "AutoWiki generates repository documentation.",
                "start_line": 1,
                "end_line": 2,
                "score": 0.95,
            },
            {
                "file": "worker/jobs.py",
                "text": "async def run_full_index(...): ...",
                "start_line": 539,
                "end_line": 566,
                "score": 0.92,
            },
        ]

    fake_store.search.side_effect = _search

    ctx = {}
    await startup(ctx)

    with (
        patch("worker.jobs.make_llm_provider", return_value=mock_llm),
        patch("worker.jobs.make_embedding_provider", return_value=mock_embedding),
        patch("worker.jobs._load_faiss_for_research", return_value=fake_store),
    ):
        try:
            await run_fast_report(
                ctx,
                repo_id="r1",
                job_id="j1",
                report_id="fr1",
                section_id="sec1",
                question="How does indexing work?",
            )

            async with get_session(db_path) as s:
                job = await s.get(Job, "j1")
                report = await s.get(FastReport, "fr1")
                section = await s.get(FastReportSection, "sec1")

                assert job is not None
                assert job.status == "done"
                assert job.progress == 100
                assert report is not None
                assert report.status == "done"
                assert report.active_section_id == "sec1"
                assert section is not None
                assert section.status == "done"
                assert section.title == "Indexing Flow"
                assert section.summary == "The worker job orchestrates indexing."
                assert section.markdown.startswith("# Indexing Flow")
                citations = json.loads(section.citations_json)
                assert citations[0]["id"] == "code-1"
                assert citations[0]["file_path"] == "worker/jobs.py"
                assert json.loads(section.evidence_blocks_json)[0]["citation_id"] == (
                    "code-1"
                )
                assert "README.md" not in section.citations_json
                assert json.loads(section.related_wiki_pages_json)[0]["slug"] == (
                    "overview"
                )
                assert "diagram" not in section.related_diagrams_json.lower()
                assert fake_store.search.call_count >= 2
                assert any(call["doc_k"] == 3 for call in search_calls)
        finally:
            await dispose_db(db_path)
            reset_config()
