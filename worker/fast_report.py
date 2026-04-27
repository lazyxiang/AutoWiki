"""Fast report domain service.

Builds a structured, evidence-driven report section from four explicit
retrieval layers:

1. Repository structure
2. Code evidence
3. Semantic retrieval
4. Curated knowledge
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from shared.fast_report_types import (
    FastReportCitation,
    FastReportDiagram,
    FastReportEvidenceBlock,
    FastReportWikiLink,
)
from worker.deep_research import format_retrieved_chunks_for_prompt
from worker.fast_report_search import (
    detect_question_language,
    normalize_fast_report_language,
)
from worker.llm.base import LLMProvider
from worker.pipeline.language import get_fast_report_language_instruction

CANONICAL_HEADINGS = [
    "Overview",
    "Core Implementation Components",
    "Key Implementation Details",
    "Execution Flow / Steps",
    "Configuration",
    "Use Cases",
    "Notes",
    "Further Explore",
]
SUPPORTED_CLAIM_LAYERS = {"repository_structure", "code_evidence"}
HEADING_ALIASES = {
    "overview": "Overview",
    "概述": "Overview",
    "core components": "Core Implementation Components",
    "implementation components": "Core Implementation Components",
    "core implementation components": "Core Implementation Components",
    "核心实现组件": "Core Implementation Components",
    "key implementation details": "Key Implementation Details",
    "implementation details": "Key Implementation Details",
    "key details": "Key Implementation Details",
    "关键实现细节": "Key Implementation Details",
    "execution flow": "Execution Flow / Steps",
    "execution flow / steps": "Execution Flow / Steps",
    "execution steps": "Execution Flow / Steps",
    "flow": "Execution Flow / Steps",
    "执行流程": "Execution Flow / Steps",
    "执行步骤": "Execution Flow / Steps",
    "执行流程 / 步骤": "Execution Flow / Steps",
    "configuration": "Configuration",
    "config": "Configuration",
    "配置": "Configuration",
    "use cases": "Use Cases",
    "use case": "Use Cases",
    "使用场景": "Use Cases",
    "notes": "Notes",
    "备注": "Notes",
    "说明": "Notes",
    "further explore": "Further Explore",
    "further reading": "Further Explore",
    "进一步阅读": "Further Explore",
    "延伸阅读": "Further Explore",
}

DISPLAY_HEADINGS = {
    "en": {heading: heading for heading in CANONICAL_HEADINGS},
    "zh": {
        "Overview": "概述",
        "Core Implementation Components": "核心实现组件",
        "Key Implementation Details": "关键实现细节",
        "Execution Flow / Steps": "执行流程 / 步骤",
        "Configuration": "配置",
        "Use Cases": "使用场景",
        "Notes": "说明",
        "Further Explore": "进一步阅读",
    },
}

DISPLAY_LINK_LABELS = {
    "en": {"wiki": "Wiki", "diagram": "Diagram"},
    "zh": {"wiki": "维基", "diagram": "图表"},
}


@dataclass(slots=True)
class FastReportQuestionIntent:
    question_type: str
    language: str = "en"
    target: str = ""
    answer_shape: str = ""
    evidence_shape: str = ""
    search_terms: list[str] = field(default_factory=list)
    retrieval_focus: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RepositoryStructureLayer:
    signals: list[str] = field(default_factory=list)
    citations: list[FastReportCitation] = field(default_factory=list)


@dataclass(slots=True)
class CodeEvidenceLayer:
    snippets: list[dict[str, Any]] = field(default_factory=list)
    citations: list[FastReportCitation] = field(default_factory=list)
    evidence_blocks: list[FastReportEvidenceBlock] = field(default_factory=list)


@dataclass(slots=True)
class SemanticRetrievalLayer:
    passages: list[str] = field(default_factory=list)
    citations: list[FastReportCitation] = field(default_factory=list)


@dataclass(slots=True)
class CuratedKnowledgeLayer:
    summaries: list[str] = field(default_factory=list)
    wiki_pages: list[FastReportWikiLink] = field(default_factory=list)
    diagrams: list[FastReportDiagram] = field(default_factory=list)


@dataclass(slots=True)
class FastReportRetrievalLayers:
    repository_structure: RepositoryStructureLayer
    code_evidence: CodeEvidenceLayer
    semantic_retrieval: SemanticRetrievalLayer
    curated_knowledge: CuratedKnowledgeLayer


@dataclass(slots=True)
class FastReportClaim:
    text: str
    citation_ids: list[str] = field(default_factory=list)
    supporting_layers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FastReportDraftSection:
    heading: str
    claims: list[FastReportClaim] = field(default_factory=list)


@dataclass(slots=True)
class FastReportSectionResult:
    title: str
    summary: str
    markdown: str
    citations: list[FastReportCitation] = field(default_factory=list)
    evidence_blocks: list[FastReportEvidenceBlock] = field(default_factory=list)
    related_wiki_pages: list[FastReportWikiLink] = field(default_factory=list)
    related_diagrams: list[FastReportDiagram] = field(default_factory=list)


RepositoryStructureRetriever = Callable[
    [str, FastReportQuestionIntent], Awaitable[RepositoryStructureLayer]
]
CodeEvidenceRetriever = Callable[
    [str, FastReportQuestionIntent], Awaitable[CodeEvidenceLayer]
]
SemanticRetriever = Callable[
    [str, FastReportQuestionIntent], Awaitable[SemanticRetrievalLayer]
]
CuratedKnowledgeRetriever = Callable[
    [str, FastReportQuestionIntent], Awaitable[CuratedKnowledgeLayer]
]

_SEARCH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "question_type": {"type": "string"},
        "target": {"type": "string"},
        "answer_shape": {"type": "string"},
        "evidence_shape": {"type": "string"},
        "search_terms": {
            "type": "array",
            "items": {"type": "string"},
        },
        "retrieval_focus": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["language", "question_type"],
}

_SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "citation_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "supporting_layers": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["text"],
                        },
                    },
                },
                "required": ["heading"],
            },
        },
        "notes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["title", "summary", "sections"],
}


async def plan_fast_report_search(
    question: str, repo_name: str, llm: LLMProvider
) -> FastReportQuestionIntent:
    """Plan fast-report retrieval with a structured, language-aware intent."""
    detected_language = normalize_fast_report_language(
        detect_question_language(question)
    )
    search_language = detected_language
    prompt = (
        "Plan the repository search strategy for fast report generation.\n"
        f"Repository: {repo_name}\n"
        f"Question: {question}\n\n"
        "Return JSON with language, question_type, target, answer_shape, "
        "evidence_shape, search_terms, and retrieval_focus.\n"
        f"Use '{search_language}' for language unless the question clearly "
        "requires a different answer language."
        f"{get_fast_report_language_instruction(search_language)}"
    )
    raw = await llm.generate_structured(prompt, _SEARCH_PLAN_SCHEMA)
    planned_language = normalize_fast_report_language(raw.get("language"))
    return FastReportQuestionIntent(
        language=planned_language or search_language,
        question_type=raw.get("question_type", "unknown"),
        target=raw.get("target", ""),
        answer_shape=raw.get("answer_shape", ""),
        evidence_shape=raw.get("evidence_shape", ""),
        search_terms=list(raw.get("search_terms", [])),
        retrieval_focus=list(raw.get("retrieval_focus", [])),
    )


async def retrieve_repository_structure_layer(
    question: str,
    intent: FastReportQuestionIntent,
    retriever: RepositoryStructureRetriever,
) -> RepositoryStructureLayer:
    return await retriever(question, intent)


async def retrieve_code_evidence_layer(
    question: str,
    intent: FastReportQuestionIntent,
    retriever: CodeEvidenceRetriever,
) -> CodeEvidenceLayer:
    return await retriever(question, intent)


async def retrieve_semantic_retrieval_layer(
    question: str,
    intent: FastReportQuestionIntent,
    retriever: SemanticRetriever,
) -> SemanticRetrievalLayer:
    return await retriever(question, intent)


async def retrieve_curated_knowledge_layer(
    question: str,
    intent: FastReportQuestionIntent,
    retriever: CuratedKnowledgeRetriever,
) -> CuratedKnowledgeLayer:
    return await retriever(question, intent)


async def retrieve_fast_report_layers(
    question: str,
    intent: FastReportQuestionIntent,
    repository_structure_retriever: RepositoryStructureRetriever,
    code_evidence_retriever: CodeEvidenceRetriever,
    semantic_retriever: SemanticRetriever,
    curated_knowledge_retriever: CuratedKnowledgeRetriever,
) -> FastReportRetrievalLayers:
    """Fetch the four context layers in parallel."""
    structure, code, semantic, curated = await asyncio.gather(
        retrieve_repository_structure_layer(
            question, intent, repository_structure_retriever
        ),
        retrieve_code_evidence_layer(question, intent, code_evidence_retriever),
        retrieve_semantic_retrieval_layer(question, intent, semantic_retriever),
        retrieve_curated_knowledge_layer(question, intent, curated_knowledge_retriever),
    )
    return FastReportRetrievalLayers(
        repository_structure=structure,
        code_evidence=code,
        semantic_retrieval=semantic,
        curated_knowledge=curated,
    )


def arbitrate_report_claims(claims: list[FastReportClaim]) -> list[FastReportClaim]:
    """Drop claims that are not backed by structure or code evidence."""
    supported: list[FastReportClaim] = []
    for claim in claims:
        if set(claim.supporting_layers) & SUPPORTED_CLAIM_LAYERS:
            supported.append(claim)
    return supported


def assemble_fast_report_markdown(
    *,
    title: str,
    summary: str,
    section_claims: dict[str, list[FastReportClaim]],
    notes: list[str],
    related_wiki_pages: list[FastReportWikiLink],
    related_diagrams: list[FastReportDiagram],
    language: str = "en",
) -> str:
    """Assemble report markdown using the canonical heading order."""
    lines = [f"# {title}", "", summary]
    display_language = normalize_fast_report_language(language)
    heading_map = DISPLAY_HEADINGS.get(display_language, DISPLAY_HEADINGS["en"])
    link_labels = DISPLAY_LINK_LABELS.get(display_language, DISPLAY_LINK_LABELS["en"])

    for heading in CANONICAL_HEADINGS:
        claim_list = section_claims.get(heading, [])
        note_lines = notes if heading == "Notes" else []
        include_further_explore = heading == "Further Explore" and (
            related_wiki_pages or related_diagrams or claim_list
        )
        if not claim_list and not note_lines and not include_further_explore:
            continue

        visible_heading = heading_map.get(heading, heading)
        lines.extend(["", f"## {visible_heading}", ""])
        for claim in claim_list:
            citation_suffix = (
                f" [{' ,'.join(claim.citation_ids)}]".replace(" ,", ",")
                if claim.citation_ids
                else ""
            )
            lines.append(f"- {claim.text}{citation_suffix}")

        if heading == "Notes":
            lines.extend(f"- {note}" for note in note_lines)

        if heading == "Further Explore":
            for page in related_wiki_pages:
                reason = f" - {page.reason}" if page.reason else ""
                lines.append(
                    f"- {link_labels['wiki']}: [{page.title}](wiki://{page.slug}){reason}"
                )
            for diagram in related_diagrams:
                reason = f" - {diagram.reason}" if diagram.reason else ""
                diagram_line = (
                    f"- {link_labels['diagram']}: "
                    f"{diagram.title} (`{diagram.type}`){reason}"
                )
                lines.append(
                    diagram_line
                )

    return "\n".join(lines).strip()


def _normalize_heading(heading: str) -> str:
    normalized = " ".join(heading.lower().replace("_", " ").split())
    return HEADING_ALIASES.get(normalized, heading.strip())


def _dedupe_citations(citations: list[FastReportCitation]) -> list[FastReportCitation]:
    seen: set[str] = set()
    result: list[FastReportCitation] = []
    for citation in citations:
        if citation.id in seen:
            continue
        seen.add(citation.id)
        result.append(citation)
    return result


def _collect_citation_ids_in_markdown_order(
    section_claims: dict[str, list[FastReportClaim]],
) -> list[str]:
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for heading in CANONICAL_HEADINGS:
        for claim in section_claims.get(heading, []):
            for citation_id in claim.citation_ids:
                if citation_id in seen:
                    continue
                seen.add(citation_id)
                ordered_ids.append(citation_id)
    return ordered_ids


def _filter_evidence_blocks(
    evidence_blocks: list[FastReportEvidenceBlock],
    citation_ids: list[str],
) -> list[FastReportEvidenceBlock]:
    blocks_by_citation: dict[str, list[FastReportEvidenceBlock]] = {}
    for block in evidence_blocks:
        blocks_by_citation.setdefault(block.citation_id, []).append(block)

    filtered: list[FastReportEvidenceBlock] = []
    for citation_id in citation_ids:
        filtered.extend(blocks_by_citation.get(citation_id, []))
    return filtered


def _tokenize_for_wiki_rank(text: str) -> set[str]:
    tokens: set[str] = set()
    cjk_runs = re.findall(
        r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+",
        text.lower(),
    )
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if token.isascii():
            if len(token) >= 3:
                tokens.add(token)
    for run in cjk_runs:
        if not run:
            continue
        tokens.add(run)
        if len(run) == 1:
            tokens.add(run)
            continue
        max_ngram = min(3, len(run))
        for size in range(2, max_ngram + 1):
            for index in range(0, len(run) - size + 1):
                tokens.add(run[index : index + size])
    return tokens


def _citation_tokens(citation: FastReportCitation) -> set[str]:
    path = citation.file_path.replace("/", " ").replace("_", " ").replace("-", " ")
    return _tokenize_for_wiki_rank(path) | _tokenize_for_wiki_rank(citation.label)


def _select_related_wiki_pages(
    *,
    question: str,
    evidence_citations: list[FastReportCitation],
    candidate_pages: list[FastReportWikiLink],
    limit: int = 3,
) -> list[FastReportWikiLink]:
    deduped_pages: list[FastReportWikiLink] = []
    seen_keys: set[tuple[str, str]] = set()
    for page in candidate_pages:
        key = (page.slug, page.title)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_pages.append(page)

    if not deduped_pages:
        return []

    question_tokens = _tokenize_for_wiki_rank(question)
    evidence_tokens: set[str] = set()
    for citation in evidence_citations:
        evidence_tokens |= _citation_tokens(citation)

    ranked_pages: list[tuple[tuple[int, int, int, str, str], FastReportWikiLink]] = []
    for page in deduped_pages:
        title_tokens = _tokenize_for_wiki_rank(page.title)
        slug_tokens = _tokenize_for_wiki_rank(page.slug.replace("-", " "))
        page_tokens = title_tokens | slug_tokens
        evidence_overlap = len(evidence_tokens & page_tokens)
        question_overlap = len(question_tokens & page_tokens)
        total_overlap = evidence_overlap + question_overlap
        if total_overlap == 0:
            continue
        ranked_pages.append(
            (
                (
                    -evidence_overlap,
                    -question_overlap,
                    -total_overlap,
                    page.title.lower(),
                    page.slug,
                ),
                page,
            )
        )

    return [page for _rank, page in sorted(ranked_pages, key=lambda item: item[0])][
        :limit
    ]


def _build_generation_prompt(
    question: str,
    repo_name: str,
    intent: FastReportQuestionIntent,
    layers: FastReportRetrievalLayers,
) -> str:
    structure_summary = "\n".join(
        f"- {signal}" for signal in layers.repository_structure.signals
    )
    code_context = format_retrieved_chunks_for_prompt(layers.code_evidence.snippets)
    semantic_summary = "\n".join(
        f"- {passage}" for passage in layers.semantic_retrieval.passages
    )
    curated_summary = "\n".join(
        [f"- {summary}" for summary in layers.curated_knowledge.summaries]
        + [
            f"- Wiki page: {page.title} ({page.slug})"
            for page in layers.curated_knowledge.wiki_pages
        ]
    )
    search_terms = "\n".join(f"- {term}" for term in intent.search_terms)
    retrieval_focus = "\n".join(f"- {item}" for item in intent.retrieval_focus)
    return (
        "Draft a fast report section as structured JSON.\n"
        f"Repository: {repo_name}\n"
        f"Question: {question}\n"
        f"Answer language: {intent.language}\n"
        f"Question type: {intent.question_type}\n"
        f"Target: {intent.target}\n"
        f"Expected answer shape: {intent.answer_shape}\n"
        f"Likely evidence shape: {intent.evidence_shape}\n\n"
        "Search terms:\n"
        f"{search_terms or '- None'}\n\n"
        "Retrieval focus:\n"
        f"{retrieval_focus or '- None'}\n\n"
        "Repository structure layer:\n"
        f"{structure_summary or '- None'}\n\n"
        "Code evidence layer:\n"
        f"{code_context}\n\n"
        "Semantic retrieval layer:\n"
        f"{semantic_summary or '- None'}\n\n"
        "Curated knowledge layer:\n"
        f"{curated_summary or '- None'}\n\n"
        "Use the canonical headings where relevant. Final claims must cite code "
        "or repository structure evidence."
        f"{get_fast_report_language_instruction(intent.language)}"
    )


def _parse_draft_sections(
    raw_sections: list[dict[str, Any]],
) -> dict[str, list[FastReportClaim]]:
    section_claims: dict[str, list[FastReportClaim]] = {}
    for raw_section in raw_sections:
        heading = _normalize_heading(raw_section.get("heading", ""))
        if not heading:
            continue
        claims = [
            FastReportClaim(
                text=claim.get("text", ""),
                citation_ids=list(claim.get("citation_ids", [])),
                supporting_layers=list(claim.get("supporting_layers", [])),
            )
            for claim in raw_section.get("claims", [])
            if claim.get("text")
        ]
        section_claims.setdefault(heading, []).extend(arbitrate_report_claims(claims))
    return section_claims


async def generate_fast_report_section(
    *,
    question: str,
    repo_name: str,
    llm: LLMProvider,
    repository_structure_retriever: RepositoryStructureRetriever,
    code_evidence_retriever: CodeEvidenceRetriever,
    semantic_retriever: SemanticRetriever,
    curated_knowledge_retriever: CuratedKnowledgeRetriever,
) -> FastReportSectionResult:
    """Generate one fast report section from layered retrieval context."""
    intent = await plan_fast_report_search(question, repo_name, llm)
    layers = await retrieve_fast_report_layers(
        question=question,
        intent=intent,
        repository_structure_retriever=repository_structure_retriever,
        code_evidence_retriever=code_evidence_retriever,
        semantic_retriever=semantic_retriever,
        curated_knowledge_retriever=curated_knowledge_retriever,
    )
    prompt = _build_generation_prompt(question, repo_name, intent, layers)
    raw = await llm.generate_structured(prompt, _SECTION_SCHEMA)
    section_claims = _parse_draft_sections(raw.get("sections", []))
    notes = list(raw.get("notes", []))
    evidence_citations = _dedupe_citations(
        layers.repository_structure.citations + layers.code_evidence.citations
    )
    related_wiki_pages = _select_related_wiki_pages(
        question=question,
        evidence_citations=evidence_citations,
        candidate_pages=list(layers.curated_knowledge.wiki_pages),
    )
    markdown = assemble_fast_report_markdown(
        title=raw.get("title", "Fast Report"),
        summary=raw.get("summary", ""),
        section_claims=section_claims,
        notes=notes,
        related_wiki_pages=related_wiki_pages,
        related_diagrams=layers.curated_knowledge.diagrams,
        language=intent.language,
    )
    citations_by_id = {
        citation.id: citation
        for citation in _dedupe_citations(
            layers.repository_structure.citations
            + layers.code_evidence.citations
            + layers.semantic_retrieval.citations
        )
    }
    citations = [
        citations_by_id[citation_id]
        for citation_id in _collect_citation_ids_in_markdown_order(section_claims)
        if citation_id in citations_by_id
    ]
    evidence_blocks = _filter_evidence_blocks(
        layers.code_evidence.evidence_blocks,
        [citation.id for citation in citations],
    )
    return FastReportSectionResult(
        title=raw.get("title", "Fast Report"),
        summary=raw.get("summary", ""),
        markdown=markdown,
        citations=citations,
        evidence_blocks=evidence_blocks,
        related_wiki_pages=related_wiki_pages,
        related_diagrams=list(layers.curated_knowledge.diagrams),
    )
