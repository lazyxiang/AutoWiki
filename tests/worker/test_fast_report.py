"""Tests for the fast report domain service."""

from __future__ import annotations

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
