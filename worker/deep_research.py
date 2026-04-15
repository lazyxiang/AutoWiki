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
from dataclasses import dataclass, field
from typing import Any

from worker.llm.base import LLMProvider

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
    "You are a senior software engineer running a deep investigation of a "
    "code repository. Decompose the user's question into {min_steps}–{max_steps} "
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
    system = _PLANNER_SYSTEM.format(min_steps=MIN_STEPS, max_steps=MAX_STEPS)
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
