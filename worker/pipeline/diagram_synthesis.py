"""Stage 6: Generate Mermaid architecture diagrams for a repository's wiki."""
from __future__ import annotations
import logging
from typing import Any

from worker.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Valid Mermaid diagram type prefixes (lowercased for case-insensitive matching).
# "pie" has no trailing space — bare `pie` is valid Mermaid syntax.
# "gantt " has a trailing space to avoid false-positive on hypothetical `ganttable`.
_VALID_DIAGRAM_TYPES = (
    "graph ", "flowchart ", "sequencediagram", "classdiagram",
    "erdiagram", "statediagram", "pie", "gantt ",
)

_SYSTEM = """You are a software architecture diagram generator.
Output ONLY valid Mermaid diagram syntax. Do not include backticks,
code fences, or any explanation — just the raw Mermaid code."""

_DIAGRAM_PROMPT_TEMPLATE = """Repository: {repo_name}

Module structure:
{module_list}

Generate a Mermaid architecture diagram showing the relationships between
these modules. Use `graph TD` or `flowchart TD` format. Show the main
modules as nodes and draw edges where one module depends on or calls another.
Keep it concise — maximum 15 nodes."""


def validate_mermaid(diagram: str) -> bool:
    """Return True if diagram starts with a known Mermaid diagram type keyword."""
    if not diagram or not diagram.strip():
        return False
    first_line = diagram.strip().split("\n")[0].strip().lower()
    return any(first_line.startswith(t) for t in _VALID_DIAGRAM_TYPES)


async def synthesize_diagrams(
    module_tree: list[dict[str, Any]],
    repo_name: str,
    llm: LLMProvider,
    max_retries: int = 3,
) -> str | None:
    """Ask the LLM to generate a Mermaid architecture diagram for the repo.

    Retries up to `max_retries` times if the output fails Mermaid validation.
    Returns the validated diagram string, or None if all retries are exhausted.
    """
    module_list = "\n".join(
        f"- {m['path']} ({len(m.get('files', []))} files)" for m in module_tree
    )
    base_prompt = _DIAGRAM_PROMPT_TEMPLATE.format(
        repo_name=repo_name, module_list=module_list
    )
    last_output = ""
    for attempt in range(max_retries):
        if attempt > 0:
            current_prompt = (
                f"{base_prompt}\n\nPrevious attempt produced invalid Mermaid:\n"
                f"{last_output}\n\nPlease output valid Mermaid syntax only."
            )
        else:
            current_prompt = base_prompt
        logger.debug("synthesize_diagrams attempt %d/%d", attempt + 1, max_retries)
        last_output = (await llm.generate(current_prompt, system=_SYSTEM)) or ""
        if validate_mermaid(last_output.strip()):
            return last_output.strip()
    logger.warning(
        "synthesize_diagrams: all %d attempts exhausted for repo %r", max_retries, repo_name
    )
    return None
