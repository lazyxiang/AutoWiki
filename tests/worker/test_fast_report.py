"""Tests for the fast report domain service."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

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
            supporting_layers=["external_source"],
        ),
        FastReportClaim(
            text="The repo is split across api, worker, and web.",
            citation_ids=["struct-1"],
            supporting_layers=["repository_structure"],
        ),
    ]

    supported = arbitrate_report_claims(
        claims,
        available_citation_ids={"code-1", "struct-1"},
    )

    assert [claim.text for claim in supported] == [
        "Indexing starts in worker/jobs.py.",
        "The repo is split across api, worker, and web.",
    ]


def test_arbitrate_report_claims_rejects_unknown_citations():
    from worker.fast_report import FastReportClaim, arbitrate_report_claims

    claims = [
        FastReportClaim(
            text="Unsupported summary.",
            citation_ids=["missing-1"],
            supporting_layers=["code_evidence"],
        )
    ]
    supported = arbitrate_report_claims(
        claims,
        available_citation_ids={"code-1"},
    )
    assert supported == []


def test_arbitrate_report_claims_rejects_claims_with_no_citations():
    from worker.fast_report import FastReportClaim, arbitrate_report_claims

    claims = [
        FastReportClaim(
            text="No citations.",
            citation_ids=[],
            supporting_layers=["code_evidence"],
        )
    ]
    supported = arbitrate_report_claims(
        claims,
        available_citation_ids={"code-1"},
    )
    assert supported == []


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


def test_detect_question_language_defaults_to_english():
    from worker.fast_report.search import detect_question_language

    assert detect_question_language("How does indexing work?") == "en"


def test_detect_question_language_switches_to_chinese_for_cjk_input():
    from worker.fast_report.search import detect_question_language

    assert detect_question_language("索引流程是怎么执行的？") == "zh"


def test_detect_question_language_treats_japanese_and_korean_as_cjk():
    from worker.fast_report.search import detect_question_language

    assert detect_question_language("インデックス処理はどう動きますか？") == "zh"
    assert detect_question_language("인덱싱 흐름은 어떻게 동작하나요?") == "zh"


def test_normalize_heading_accepts_chinese_aliases():
    from worker.fast_report import _normalize_heading

    assert _normalize_heading("概述") == "Overview"
    assert _normalize_heading("执行流程 / 步骤") == "Execution Flow / Steps"


def test_search_plan_schema_constrains_question_type_to_enum():
    from worker.fast_report import _SEARCH_PLAN_SCHEMA
    from worker.fast_report.planning import QUESTION_TYPES

    qt_schema = _SEARCH_PLAN_SCHEMA["properties"]["question_type"]

    assert set(qt_schema["enum"]) == set(QUESTION_TYPES)


def test_retrieve_code_evidence_prefers_symbol_seed_and_expands_call_chain():
    from worker.fast_report import FastReportQuestionIntent
    from worker.fast_report.search import retrieve_code_evidence

    index = {
        "readme_headings": ["AutoWiki", "Indexing"],
        "files": {
            "worker/jobs.py": {
                "path": "worker/jobs.py",
                "tokens": [
                    "worker",
                    "jobs",
                    "run",
                    "full",
                    "index",
                    "indexing",
                ],
                "imports": ["worker/pipeline/ingestion.py"],
                "imported_by": ["worker/main.py", "tests/worker/test_jobs.py"],
                "external_deps": [],
                "entities": [
                    {
                        "name": "run_full_index",
                        "type": "function",
                        "start_line": 10,
                        "end_line": 42,
                        "signature": "run_full_index(ctx, repo_id)",
                        "docstring": "Coordinate the indexing pipeline.",
                        "symbol_path": "worker.jobs.run_full_index",
                    }
                ],
                "is_test": False,
                "is_config": False,
            },
            "worker/pipeline/ingestion.py": {
                "path": "worker/pipeline/ingestion.py",
                "tokens": [
                    "worker",
                    "pipeline",
                    "ingestion",
                    "clone",
                    "fetch",
                    "repository",
                ],
                "imports": ["worker/pipeline/ast_analysis.py"],
                "imported_by": ["worker/jobs.py"],
                "external_deps": [],
                "entities": [
                    {
                        "name": "clone_or_fetch",
                        "type": "function",
                        "start_line": 5,
                        "end_line": 24,
                        "signature": "clone_or_fetch(repo_url)",
                        "docstring": "Prepare the repository snapshot.",
                        "symbol_path": "worker.pipeline.ingestion.clone_or_fetch",
                    }
                ],
                "is_test": False,
                "is_config": False,
            },
            "worker/pipeline/ast_analysis.py": {
                "path": "worker/pipeline/ast_analysis.py",
                "tokens": [
                    "worker",
                    "pipeline",
                    "ast",
                    "analysis",
                    "analyze",
                    "files",
                ],
                "imports": [],
                "imported_by": ["worker/pipeline/ingestion.py"],
                "external_deps": [],
                "entities": [
                    {
                        "name": "analyze_all_files",
                        "type": "function",
                        "start_line": 30,
                        "end_line": 80,
                        "signature": "analyze_all_files(files)",
                        "docstring": "Build AST summaries for source files.",
                        "symbol_path": (
                            "worker.pipeline.ast_analysis.analyze_all_files"
                        ),
                    }
                ],
                "is_test": False,
                "is_config": False,
            },
            "tests/worker/test_jobs.py": {
                "path": "tests/worker/test_jobs.py",
                "tokens": ["tests", "worker", "jobs", "index"],
                "imports": ["worker/jobs.py"],
                "imported_by": [],
                "external_deps": [],
                "entities": [],
                "is_test": True,
                "is_config": False,
            },
        },
    }
    plan = FastReportQuestionIntent(
        question_type="execution_flow",
        language="en",
        target="indexing pipeline",
        answer_shape="report",
        evidence_shape="call-chain",
        search_terms=["indexing", "clone_or_fetch"],
        retrieval_focus=["worker.jobs.run_full_index"],
    )

    layer = retrieve_code_evidence(
        index,
        plan,
        "How does run_full_index trigger clone_or_fetch during indexing?",
    )

    assert [citation.file_path for citation in layer.citations] == [
        "worker/jobs.py",
        "worker/pipeline/ingestion.py",
        "worker/pipeline/ast_analysis.py",
    ]
    assert [block.symbol_path for block in layer.evidence_blocks] == [
        "worker.jobs.run_full_index",
        "worker.pipeline.ingestion.clone_or_fetch",
        "worker.pipeline.ast_analysis.analyze_all_files",
    ]
    assert [snippet["file"] for snippet in layer.snippets] == [
        "worker/jobs.py",
        "worker/pipeline/ingestion.py",
        "worker/pipeline/ast_analysis.py",
    ]
    assert all("tests/worker/test_jobs.py" != c.file_path for c in layer.citations)


def test_retrieve_code_evidence_keeps_relevant_config_files_for_config_questions():
    from worker.fast_report import FastReportQuestionIntent
    from worker.fast_report.search import retrieve_code_evidence

    index = {
        "files": {
            "pyproject.toml": {
                "path": "pyproject.toml",
                "tokens": [
                    "pyproject",
                    "settings",
                    "configuration",
                    "config",
                    "env",
                    "provider",
                ],
                "imports": [],
                "imported_by": [],
                "external_deps": [],
                "entities": [],
                "is_test": False,
                "is_config": True,
            },
            "worker/settings.py": {
                "path": "worker/settings.py",
                "tokens": [
                    "worker",
                    "settings",
                    "configuration",
                    "config",
                    "env",
                    "provider",
                    "openai",
                ],
                "imports": [],
                "imported_by": [],
                "external_deps": [],
                "entities": [
                    {
                        "name": "LLMConfig",
                        "type": "class",
                        "start_line": 8,
                        "end_line": 34,
                        "signature": "class LLMConfig",
                        "docstring": "Model and provider settings.",
                        "symbol_path": "worker.settings.LLMConfig",
                    }
                ],
                "is_test": False,
                "is_config": True,
            },
        }
    }
    plan = FastReportQuestionIntent(
        question_type="configuration",
        language="en",
        target="provider settings",
        answer_shape="report",
        evidence_shape="config",
        search_terms=["configuration", "settings", "env", "provider"],
        retrieval_focus=[],
    )

    layer = retrieve_code_evidence(
        index,
        plan,
        "Which configuration settings and env values control the provider setup?",
    )

    assert [citation.file_path for citation in layer.citations] == [
        "worker/settings.py",
        "pyproject.toml",
    ]
    assert layer.evidence_blocks[0].symbol_path == "worker.settings.LLMConfig"


async def test_plan_fast_report_search_appends_language_instruction(mock_llm):
    from worker.fast_report import plan_fast_report_search

    captured: dict[str, str] = {}

    async def _structured(prompt, schema):
        captured["prompt"] = prompt
        return {
            "language": "zh",
            "question_type": "execution_flow",
            "target": "索引流程",
            "answer_shape": "report",
            "evidence_shape": "entry-points",
            "search_terms": ["indexing"],
            "retrieval_focus": ["worker/jobs.py"],
        }

    mock_llm.generate_structured.side_effect = _structured

    await plan_fast_report_search("索引流程是怎么执行的？", "autowiki", mock_llm)

    assert "IMPORTANT: Write the fast report body in Chinese" in captured["prompt"]


async def test_plan_fast_report_search_normalizes_common_chinese_variants(mock_llm):
    from worker.fast_report import plan_fast_report_search

    async def _structured(prompt, schema):
        return {
            "language": "zh-CN",
            "question_type": "execution_flow",
            "target": "索引流程",
            "answer_shape": "report",
            "evidence_shape": "entry-points",
        }

    mock_llm.generate_structured.side_effect = _structured

    intent = await plan_fast_report_search(
        "索引流程是怎么执行的？", "autowiki", mock_llm
    )

    assert intent.language == "zh"


async def test_plan_fast_report_search_normalizes_japanese_and_korean_variants(
    mock_llm,
):
    from worker.fast_report import plan_fast_report_search

    responses = iter(
        [
            {
                "language": "ja-JP",
                "question_type": "execution_flow",
                "target": "indexing flow",
                "answer_shape": "report",
                "evidence_shape": "entry-points",
            },
            {
                "language": "ko-KR",
                "question_type": "execution_flow",
                "target": "indexing flow",
                "answer_shape": "report",
                "evidence_shape": "entry-points",
            },
        ]
    )

    async def _structured(prompt, schema):
        return next(responses)

    mock_llm.generate_structured.side_effect = _structured

    ja_intent = await plan_fast_report_search(
        "インデックス処理はどう動きますか？", "autowiki", mock_llm
    )
    ko_intent = await plan_fast_report_search(
        "인덱싱 흐름은 어떻게 동작하나요?", "autowiki", mock_llm
    )

    assert ja_intent.language == "zh"
    assert ko_intent.language == "zh"


async def test_plan_fast_report_search_injects_repo_shape_context(mock_llm):
    from worker.fast_report import plan_fast_report_search

    captured: dict[str, str] = {}

    async def _structured(prompt, schema):
        captured["prompt"] = prompt
        return {
            "language": "en",
            "question_type": "execution_flow",
            "search_terms": ["run"],
            "retrieval_focus": ["worker.fast_report.generate_fast_report_section"],
        }

    mock_llm.generate_structured.side_effect = _structured

    await plan_fast_report_search(
        "How does the report flow run?",
        "autowiki",
        mock_llm,
        index={
            "directory_tree": "worker/\n  fast_report.py\n",
            "hub_modules": [
                {
                    "path": "worker/fast_report.py",
                    "in_degree": 4,
                    "purpose": "Fast reports.",
                }
            ],
            "readme_headings": ["AutoWiki", "Architecture"],
        },
    )

    assert "Directory tree:" in captured["prompt"]
    assert "worker/fast_report.py" in captured["prompt"]
    assert "Hub modules:" in captured["prompt"]
    assert "README headings:" in captured["prompt"]


async def test_plan_fast_report_search_retries_degenerate_output_once(mock_llm):
    from worker.fast_report import plan_fast_report_search

    prompts: list[str] = []
    responses = iter(
        [
            {
                "language": "en",
                "question_type": "unknown",
                "search_terms": [],
                "retrieval_focus": [],
            },
            {
                "language": "en",
                "question_type": "execution_flow",
                "search_terms": ["run"],
                "retrieval_focus": ["worker.fast_report.plan_fast_report_search"],
            },
        ]
    )

    async def _structured(prompt, schema):
        prompts.append(prompt)
        return next(responses)

    mock_llm.generate_structured.side_effect = _structured

    intent = await plan_fast_report_search(
        "How does X work?",
        "autowiki",
        mock_llm,
        index={"directory_tree": "worker/\n  fast_report.py\n"},
    )

    assert len(prompts) == 2
    assert intent.question_type == "execution_flow"
    assert "Re-plan the search" in prompts[1]


async def test_plan_fast_report_search_coerces_invalid_question_type_to_unknown(
    mock_llm,
):
    from worker.fast_report import plan_fast_report_search

    async def _structured(prompt, schema):
        return {
            "language": "en",
            "question_type": "how_x_works",
            "search_terms": ["x"],
            "retrieval_focus": ["worker.fast_report"],
        }

    mock_llm.generate_structured.side_effect = _structured

    intent = await plan_fast_report_search("How does X work?", "autowiki", mock_llm)

    assert intent.question_type == "unknown"


def test_select_related_wiki_pages_supports_localized_tokens():
    from worker.fast_report import _select_related_wiki_pages

    pages = [
        FastReportWikiLink(
            slug="执行流程",
            title="执行流程",
            reason="索引步骤说明",
        ),
        FastReportWikiLink(
            slug="概述",
            title="概述",
            reason="系统总览",
        ),
    ]

    selected = _select_related_wiki_pages(
        question="索引的执行流程是什么？",
        evidence_citations=[
            FastReportCitation(
                id="code-1",
                file_path="worker/jobs.py",
                start_line=1,
                end_line=20,
                label="执行流程",
                kind="code_evidence",
            )
        ],
        candidate_pages=pages,
    )

    assert [page.slug for page in selected] == ["执行流程"]


def test_select_related_wiki_pages_matches_partial_cjk_phrase_overlap():
    from worker.fast_report import _select_related_wiki_pages

    pages = [
        FastReportWikiLink(
            slug="执行流程",
            title="执行流程",
            reason="索引步骤说明",
        ),
        FastReportWikiLink(
            slug="配置",
            title="配置",
            reason="配置说明",
        ),
    ]

    selected = _select_related_wiki_pages(
        question="索引的执行流程是什么？",
        evidence_citations=[
            FastReportCitation(
                id="code-1",
                file_path="worker/jobs.py",
                start_line=1,
                end_line=20,
                label="run_full_index",
                kind="code_evidence",
            )
        ],
        candidate_pages=pages,
    )

    assert [page.slug for page in selected] == ["执行流程"]


def test_select_related_wiki_pages_matches_two_character_ascii_signal():
    from worker.fast_report import _select_related_wiki_pages

    pages = [
        FastReportWikiLink(
            slug="ui-architecture",
            title="UI Architecture",
            reason="Frontend structure",
        ),
        FastReportWikiLink(
            slug="api-overview",
            title="API Overview",
            reason="Backend endpoints",
        ),
    ]

    selected = _select_related_wiki_pages(
        question="How does the UI render repository pages?",
        evidence_citations=[
            FastReportCitation(
                id="code-1",
                file_path="web/components/wiki_page.tsx",
                start_line=1,
                end_line=20,
                label="WikiPage",
                kind="code_evidence",
            )
        ],
        candidate_pages=pages,
    )

    assert [page.slug for page in selected] == ["ui-architecture"]


def test_service_uses_public_tokenizer_for_wiki_ranking():
    import worker.fast_report.service as service

    assert service._tokenize_for_wiki_rank("UI DB S3 Go") == {
        "ui",
        "db",
        "s3",
        "go",
    }
    assert not hasattr(service, "_tokenize_fast_report")


def test_generation_prompt_includes_interpretive_block_with_no_cite_warning():
    from worker.fast_report import (
        CodeEvidenceLayer,
        CuratedKnowledgeLayer,
        FastReportQuestionIntent,
        FastReportRetrievalLayers,
        RepositoryStructureLayer,
        _build_generation_prompt,
    )
    from worker.fast_report.interpretive import InterpretiveBundle

    layers = FastReportRetrievalLayers(
        repository_structure=RepositoryStructureLayer(
            signals=["Directory tree:\nworker/\n  fast_report.py"]
        ),
        code_evidence=CodeEvidenceLayer(),
        curated_knowledge=CuratedKnowledgeLayer(),
        interpretive=InterpretiveBundle(
            entries=[
                {
                    "source": "module_docstring",
                    "file": "worker/fast_report.py",
                    "name": None,
                    "text": "Fast report domain service.",
                },
                {
                    "source": "readme_section",
                    "heading": "Architecture",
                    "text": "AutoWiki uses a 6-stage pipeline.",
                },
            ]
        ),
    )

    prompt = _build_generation_prompt(
        "How does X work?",
        "autowiki",
        FastReportQuestionIntent(question_type="execution_flow"),
        layers,
    )

    assert "Interpretive context layer:" in prompt
    assert (
        "Module docstring (worker/fast_report.py): Fast report domain service."
        in prompt
    )
    assert 'README section "Architecture": AutoWiki uses a 6-stage pipeline.' in prompt
    assert (
        "Use this interpretive layer ONLY to explain or connect code evidence" in prompt
    )
    assert "Final claims must cite repository_structure or code_evidence ids" in prompt


def test_arbitration_drops_claim_supported_only_by_interpretive_id():
    from worker.fast_report import FastReportClaim, arbitrate_report_claims

    interpretive_only = FastReportClaim(
        text="Docstring-only claim",
        citation_ids=["interp-1"],
        supporting_layers=["interpretive"],
    )
    code_backed = FastReportClaim(
        text="Code-backed claim",
        citation_ids=["code-0-0"],
        supporting_layers=["code_evidence"],
    )

    kept = arbitrate_report_claims(
        [interpretive_only, code_backed], available_citation_ids={"code-0-0"}
    )

    assert kept == [code_backed]


async def test_generate_fast_report_section_returns_structured_section(mock_llm):
    from worker.fast_report import (
        CodeEvidenceLayer,
        CuratedKnowledgeLayer,
        FastReportQuestionIntent,
        RepositoryStructureLayer,
        generate_fast_report_section,
    )

    async def _structured(*args, **kwargs):
        prompt = args[0]
        if "Plan the repository search strategy" in prompt:
            return {
                "language": "en",
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
                            "supporting_layers": ["external_source"],
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
    assert result.related_wiki_pages == []
    assert [diagram.id for diagram in result.related_diagrams] == ["diagram-1"]


async def test_run_fast_report_persists_completed_section(
    tmp_path, monkeypatch, mock_llm
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
    (repo_root / "worker").mkdir()
    (repo_root / "worker" / "jobs.py").write_text(
        "def run_full_index(ctx, repo_id, job_id):\n"
        "    return clone_or_fetch(repo_id)\n"
    )
    (repo_root / "worker" / "pipeline").mkdir()
    (repo_root / "worker" / "pipeline" / "ingestion.py").write_text(
        "def clone_or_fetch(repo_url):\n    return repo_url\n"
    )
    ast_dir = tmp_path / "repos" / "r1" / "ast"
    ast_dir.mkdir(parents=True)
    (ast_dir / "fast_report_index.json").write_text(
        json.dumps(
            {
                "index_version": 2,
                "readme_headings": ["AutoWiki", "Indexing overview"],
                "files": {
                    "worker/jobs.py": {
                        "path": "worker/jobs.py",
                        "tokens": [
                            "worker",
                            "jobs",
                            "run",
                            "full",
                            "index",
                            "indexing",
                        ],
                        "imports": ["worker/pipeline/ingestion.py"],
                        "imported_by": [],
                        "external_deps": [],
                        "entities": [
                            {
                                "name": "run_full_index",
                                "type": "function",
                                "start_line": 1,
                                "end_line": 2,
                                "signature": "run_full_index(ctx, repo_id, job_id)",
                                "docstring": "Coordinate the indexing pipeline.",
                                "symbol_path": "worker.jobs.run_full_index",
                            }
                        ],
                        "is_test": False,
                        "is_config": False,
                    },
                    "worker/pipeline/ingestion.py": {
                        "path": "worker/pipeline/ingestion.py",
                        "tokens": [
                            "worker",
                            "pipeline",
                            "ingestion",
                            "clone",
                            "fetch",
                            "indexing",
                        ],
                        "imports": [],
                        "imported_by": ["worker/jobs.py"],
                        "external_deps": [],
                        "entities": [
                            {
                                "name": "clone_or_fetch",
                                "type": "function",
                                "start_line": 1,
                                "end_line": 2,
                                "signature": "clone_or_fetch(repo_url)",
                                "docstring": "Clone the repository snapshot.",
                                "symbol_path": (
                                    "worker.pipeline.ingestion.clone_or_fetch"
                                ),
                            }
                        ],
                        "is_test": False,
                        "is_config": False,
                    },
                },
            }
        )
    )

    async def _structured(*args, **kwargs):
        prompt = args[0]
        if "Plan the repository search strategy" in prompt:
            return {
                "language": "en",
                "question_type": "execution_flow",
                "target": "indexing",
                "answer_shape": "report",
                "evidence_shape": "entry-points",
                "search_terms": ["indexing", "clone_or_fetch"],
                "retrieval_focus": ["worker.jobs.run_full_index"],
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
                            "citation_ids": ["code-1-0"],
                            "supporting_layers": ["code_evidence"],
                        }
                    ],
                }
            ],
            "notes": ["Uses the queued fast report background flow."],
        }

    mock_llm.generate_structured.side_effect = _structured

    ctx = {}
    await startup(ctx)

    with patch("worker.fast_report.jobs.make_llm_provider", return_value=mock_llm):
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
                assert citations[0]["id"] == "code-1-0"
                assert citations[0]["file_path"] == "worker/jobs.py"
                evidence_blocks = json.loads(section.evidence_blocks_json)
                assert evidence_blocks[0]["citation_id"] == "code-1-0"
                assert evidence_blocks[0]["symbol_path"] == "worker.jobs.run_full_index"
                trace = json.loads(section.analysis_trace_json)
                phases = [event["phase"] for event in trace["events"]]
                assert "index_check" in phases
                assert "search_plan" in phases
                assert "slice_extraction" in phases
                assert "interpretive_layer" in phases
                assert "generation" in phases
                assert "arbitration" in phases
                assert "README.md" not in section.citations_json
                assert json.loads(section.related_wiki_pages_json) == []
                assert "diagram" not in section.related_diagrams_json.lower()
        finally:
            await dispose_db(db_path)
            reset_config()


def test_worker_jobs_reexports_split_fast_report_entrypoints():
    from worker.fast_report import jobs as fast_report_job_module
    from worker.jobs import (
        FastReportIndexOutdated,
        _build_default_fast_report_retrievers,
        _validate_fast_report_index_version,
        run_fast_report,
    )

    assert run_fast_report is fast_report_job_module.run_fast_report
    assert FastReportIndexOutdated is fast_report_job_module.FastReportIndexOutdated
    assert (
        _validate_fast_report_index_version
        is fast_report_job_module._validate_fast_report_index_version
    )
    assert (
        _build_default_fast_report_retrievers
        is fast_report_job_module._build_default_fast_report_retrievers
    )


def test_validate_fast_report_index_rejects_missing_version():
    from worker.jobs import FastReportIndexOutdated, _validate_fast_report_index_version

    with pytest.raises(FastReportIndexOutdated) as exc_info:
        _validate_fast_report_index_version({})

    assert "fast_report_index_outdated" in str(exc_info.value)


def test_validate_fast_report_index_rejects_v1():
    from worker.jobs import FastReportIndexOutdated, _validate_fast_report_index_version

    with pytest.raises(FastReportIndexOutdated) as exc_info:
        _validate_fast_report_index_version({"index_version": 1})

    assert "fast_report_index_outdated" in str(exc_info.value)


def test_validate_fast_report_index_accepts_v2():
    from worker.jobs import _validate_fast_report_index_version

    _validate_fast_report_index_version({"index_version": 2})


async def test_build_default_fast_report_retrievers_rejects_corrupt_index_file(
    tmp_path, monkeypatch
):
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Repository, WikiPage
    from worker.jobs import (
        FastReportIndexOutdated,
        _build_default_fast_report_retrievers,
    )

    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    from shared.config import get_config, reset_config

    reset_config()
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="ready"))
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
    (repo_root / "README.md").write_text("# AutoWiki\n\n## Runtime\n\nFallback docs.")
    (repo_root / "worker").mkdir()
    ast_dir = tmp_path / "repos" / "r1" / "ast"
    ast_dir.mkdir(parents=True)
    (ast_dir / "fast_report_index.json").write_text('{"files": ')

    cfg = get_config()

    try:
        with pytest.raises(FastReportIndexOutdated):
            await _build_default_fast_report_retrievers(
                repo_id="r1",
                db_path=db_path,
                cfg=cfg,
            )
    finally:
        await dispose_db(db_path)
        reset_config()


async def test_build_default_fast_report_retrievers_rejects_non_utf8_index_bytes(
    tmp_path, monkeypatch
):
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Repository, WikiPage
    from worker.jobs import (
        FastReportIndexOutdated,
        _build_default_fast_report_retrievers,
    )

    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    from shared.config import get_config, reset_config

    reset_config()
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="ready"))
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
    (repo_root / "README.md").write_text("# AutoWiki\n\n## Runtime\n\nFallback docs.")
    (repo_root / "worker").mkdir()
    ast_dir = tmp_path / "repos" / "r1" / "ast"
    ast_dir.mkdir(parents=True)
    (ast_dir / "fast_report_index.json").write_bytes(b"\xff\xfe\xfa")

    cfg = get_config()

    try:
        with pytest.raises(FastReportIndexOutdated):
            await _build_default_fast_report_retrievers(
                repo_id="r1",
                db_path=db_path,
                cfg=cfg,
            )
    finally:
        await dispose_db(db_path)
        reset_config()
