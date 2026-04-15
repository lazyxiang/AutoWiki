"""Deep Research orchestrator.

Implements the three-stage research flow:

    User question
          │
          ▼
    Research Planner (LLM)  → list[ResearchStep]
          │
          ▼  (investigator loop, one round per step)
    Investigator Agent     → list[ResearchFinding]
          │
          ▼
    Synthesizer (LLM)       → final Markdown report

Each helper is a pure async function — no DB, no WebSocket. The ARQ job
in ``worker.jobs`` wires these together and streams events via the
``on_event`` callback.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from worker.embedding.base import EmbeddingProvider
from worker.llm.base import LLMProvider
from worker.pipeline.rag_indexer import FAISSStore

# Event payload emitted to WebSocket / CLI consumers.
ResearchEvent = dict[str, Any]
OnEventCallback = Callable[[ResearchEvent], Awaitable[None]]

MAX_STEPS = 5
MIN_STEPS = 3


@dataclass
class ResearchStep:
    """One investigation step from the research plan."""

    query: str
    rationale: str


@dataclass
class ResearchFinding:
    """Result of running the investigator against one step."""

    step_index: int
    query: str
    answer: str
    sources: list[dict] = field(default_factory=list)


_PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["query", "rationale"],
            },
        }
    },
    "required": ["plan"],
}

_PLANNER_SYSTEM = (
    f"You are a senior software engineer running a deep investigation of a "
    f"code repository. Decompose the user's question into {MIN_STEPS}–{MAX_STEPS} "
    "focused investigation steps. Each step should be answerable by a single "
    "RAG search against the repository. Output ONLY valid JSON."
)


async def plan_research(
    question: str,
    repo_name: str,
    readme: str | None,
    llm: LLMProvider,
) -> list[ResearchStep]:
    """Decompose a research question into an ordered list of investigation steps."""
    system = _PLANNER_SYSTEM
    prompt = (
        f"Repository: {repo_name}\n\n"
        f"README excerpt:\n{(readme or '')[:2000]}\n\n"
        f"Research question:\n{question}\n\n"
        f"Produce {MIN_STEPS}–{MAX_STEPS} investigation steps as JSON with "
        "key 'plan'. Each entry must have 'query' (a specific question) and "
        "'rationale' (why this step is needed)."
    )
    result = await llm.generate_structured(prompt, _PLANNER_SCHEMA, system=system)
    raw_steps = result.get("plan", [])[:MAX_STEPS]
    return [ResearchStep(query=s["query"], rationale=s["rationale"]) for s in raw_steps]


_INVESTIGATOR_SYSTEM = (
    "You are investigating one question about a software repository. "
    "Answer precisely using ONLY the provided source code context. Cite the "
    "file names you relied on. If the context is insufficient, say so."
)


async def investigate_step(
    step: ResearchStep,
    step_index: int,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    top_k: int = 8,
) -> ResearchFinding:
    """Run RAG retrieval + LLM answer for a single investigation step."""
    query_vec = await embedding.embed(step.query)
    chunks = store.search(query_vec, k=top_k)

    context = "\n\n---\n\n".join(
        f"File: {c.get('file', 'unknown')} (lines {c.get('start_line', '?')}+)\n"
        f"{c.get('text', '')}"
        for c in chunks
    )

    prompt = (
        f"Investigation step: {step.query}\n"
        f"Rationale: {step.rationale}\n\n"
        f"Source code context:\n{context}\n\n"
        "Answer the investigation step using the context above. Cite file names."
    )
    answer = await llm.generate(prompt, system=_INVESTIGATOR_SYSTEM)
    return ResearchFinding(
        step_index=step_index,
        query=step.query,
        answer=answer,
        sources=[
            {
                "file": c.get("file", "unknown"),
                "start_line": c.get("start_line"),
                "end_line": c.get("end_line"),
            }
            for c in chunks
        ],
    )


_SYNTHESIZER_SYSTEM = (
    "You are compiling a deep-research report for a software engineer. "
    "Using the original question, the investigation plan, and the per-step "
    "findings, write a well-structured Markdown report. Include: a short "
    "executive summary, the conclusions for each step, a combined answer "
    "section, and an explicit 'Sources' section that lists every file "
    "referenced. Use Markdown headings, bullet points, and code fences as "
    "appropriate."
)


async def synthesize_report(
    question: str,
    plan: list[ResearchStep],
    findings: list[ResearchFinding],
    llm: LLMProvider,
) -> str:
    """Generate the final Markdown report for a Deep Research job."""
    plan_md = "\n".join(
        f"{i + 1}. **{s.query}** — {s.rationale}" for i, s in enumerate(plan)
    )
    findings_md = "\n\n".join(
        f"### Step {f.step_index + 1}: {f.query}\n\n{f.answer}\n\n"
        f"Sources: {', '.join(s.get('file', '?') for s in f.sources)}"
        for f in findings
    )
    prompt = (
        f"Research question:\n{question}\n\n"
        f"Investigation plan:\n{plan_md}\n\n"
        f"Findings:\n{findings_md}\n\n"
        "Produce the final report in Markdown."
    )
    return await llm.generate(prompt, system=_SYNTHESIZER_SYSTEM)


@dataclass
class ResearchResult:
    """Aggregate result returned by the orchestrator."""

    plan: list[ResearchStep]
    findings: list[ResearchFinding]
    report: str

    def to_serialisable(self) -> dict:
        return {
            "plan": [asdict(s) for s in self.plan],
            "findings": [asdict(f) for f in self.findings],
            "report": self.report,
        }


async def run_deep_research_flow(
    question: str,
    repo_name: str,
    readme: str | None,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    on_event: OnEventCallback | None = None,
) -> ResearchResult:
    """Drive planner → investigator → synthesizer, emitting progress events."""

    async def _emit(event: ResearchEvent) -> None:
        if on_event is not None:
            await on_event(event)

    plan = await plan_research(question, repo_name, readme, llm)
    await _emit({"type": "plan", "plan": [asdict(s) for s in plan]})

    findings: list[ResearchFinding] = []
    for idx, step in enumerate(plan):
        await _emit(
            {
                "type": "step_start",
                "step_index": idx,
                "query": step.query,
                "rationale": step.rationale,
            }
        )
        finding = await investigate_step(
            step=step,
            step_index=idx,
            store=store,
            llm=llm,
            embedding=embedding,
        )
        findings.append(finding)
        await _emit(
            {
                "type": "step_finding",
                "step_index": idx,
                "answer": finding.answer,
                "sources": finding.sources,
            }
        )

    report = await synthesize_report(question, plan, findings, llm)
    await _emit({"type": "report", "content": report})

    return ResearchResult(plan=plan, findings=findings, report=report)
