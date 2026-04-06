"""Stage 7 of the generation pipeline: Mermaid architecture diagram synthesis.

Takes the :class:`~worker.pipeline.wiki_planner.WikiPlan` produced by Stage 5
and asks the configured LLM to generate a Mermaid diagram that visualises
the module/component relationships in the repository.

The output is a raw Mermaid string (no code fences) that is saved to
``~/.autowiki/repos/{repo_hash}/ast/architecture.mmd`` by the calling stage
orchestrator.

Typical usage::

    from worker.pipeline.diagram_synthesis import synthesize_diagrams

    diagram = await synthesize_diagrams(wiki_plan, repo_name="myorg/myrepo", llm=llm)
    if diagram:
        (repo_dir / "ast" / "architecture.mmd").write_text(diagram)
"""

from __future__ import annotations

import logging

from worker.llm.base import LLMProvider
from worker.pipeline.language import get_language_instruction
from worker.pipeline.wiki_planner import WikiPlan
from worker.utils.mermaid import sanitize_mermaid

logger = logging.getLogger(__name__)

# Tuple of known Mermaid diagram-type prefix strings (all lower-cased) used by
# :func:`validate_mermaid` to determine whether the LLM output looks like real
# Mermaid syntax.  Each entry is the lowercase start of a valid first line.
# Notes on specific entries:
#   - "pie" has no trailing space because bare `pie` is valid Mermaid syntax.
#   - "gantt " has a trailing space to prevent a false positive on any
#     hypothetical future keyword that starts with "gantt" (e.g. "ganttable").
_VALID_DIAGRAM_TYPES = (
    "graph ",
    "flowchart ",
    "sequencediagram",
    "classdiagram",
    "erdiagram",
    "statediagram",
    "pie",
    "gantt ",
)

_SYSTEM = """You are a software architecture diagram generator.
Output ONLY valid Mermaid diagram syntax. Do not include backticks,
code fences, or any explanation — just the raw Mermaid code.

IMPORTANT Mermaid quoting rules — violating these causes parse errors:
- Node labels with special chars MUST be quoted:
  A["MCP Server (stdio)"] NOT A[MCP Server (stdio)]
- Edge labels with special chars MUST be quoted:
  -->|"GET /status/{id}"| NOT -->|GET /status/{id}|
Special characters that require quoting: ( ) { } | < > /"""

_DIAGRAM_PROMPT_TEMPLATE = """Repository: {repo_name}

Module structure:
{module_list}

Generate a Mermaid architecture diagram showing the relationships between
these modules. Use `graph TD` or `flowchart TD` format. Show the main
modules as nodes and draw edges where one module depends on or calls another.
Keep it concise — maximum 15 nodes."""


def validate_mermaid(diagram: str) -> bool:
    """Check whether *diagram* begins with a recognised Mermaid diagram type.

    Strips leading/trailing whitespace from the entire string and from the
    first line, then performs a case-insensitive prefix check against
    :data:`_VALID_DIAGRAM_TYPES`.  Does **not** perform full Mermaid syntax
    validation — it only guards against the LLM returning prose, code fences,
    or an explanation instead of raw Mermaid code.

    Args:
        diagram: The raw string returned by the LLM.  May contain leading
            whitespace or trailing newlines.

    Returns:
        ``True`` if the first non-empty line (lowercased) starts with any
        entry in :data:`_VALID_DIAGRAM_TYPES`; ``False`` otherwise, including
        when *diagram* is empty or whitespace-only.

    Example::

        validate_mermaid("graph TD\\n  A --> B")   # True
        validate_mermaid("flowchart LR\\n  X --> Y") # True
        validate_mermaid("```mermaid\\ngraph TD")    # False (code fence)
        validate_mermaid("")                         # False (empty)
        validate_mermaid("Here is the diagram:")     # False (prose)
    """
    if not diagram or not diagram.strip():
        return False
    first_line = diagram.strip().split("\n")[0].strip().lower()
    return any(first_line.startswith(t) for t in _VALID_DIAGRAM_TYPES)


async def synthesize_diagrams(
    wiki_plan: WikiPlan,
    repo_name: str,
    llm: LLMProvider,
    max_retries: int = 3,
    wiki_language: str = "en",
) -> str | None:
    """Ask the LLM to generate a Mermaid architecture diagram for the repo.

    Builds a prompt from the wiki plan's page list (titles, parent
    relationships, and file counts), then calls the LLM.  The response is
    validated with :func:`validate_mermaid`; if validation fails the LLM is
    called again with a retry prompt that includes the invalid previous output.

    Retry behaviour:
        On the first attempt, *base_prompt* is sent as-is.  On each subsequent
        attempt (up to *max_retries* - 1 retries), the prompt is augmented with
        ``"Previous attempt produced invalid Mermaid: {last_output}"`` so the
        LLM can self-correct.  If all *max_retries* attempts fail validation,
        a warning is logged and ``None`` is returned.

    Args:
        wiki_plan: The :class:`~worker.pipeline.wiki_planner.WikiPlan`
            produced by Stage 5.  Its ``pages`` list is used to build the
            module-structure section of the prompt.
        repo_name: Human-readable repository identifier included verbatim in
            the prompt (e.g. ``"myorg/myrepo"``).
        llm: An :class:`~worker.llm.base.LLMProvider` instance used to call
            the configured language model.
        max_retries: Total number of LLM calls to make before giving up.
            Defaults to ``3``.

    Returns:
        A validated Mermaid diagram ``str`` (stripped of surrounding
        whitespace) on success, or ``None`` if every attempt produced output
        that did not pass :func:`validate_mermaid`.

    Example::

        diagram = await synthesize_diagrams(
            wiki_plan=plan,
            repo_name="acme/backend",
            llm=llm,
            max_retries=3,
        )
        if diagram:
            # diagram == "graph TD\\n  API --> DB\\n  API --> Cache"
            Path("architecture.mmd").write_text(diagram)
        else:
            # All 3 attempts returned invalid Mermaid output
            logger.warning("No diagram generated for acme/backend")
    """
    module_list = "\n".join(
        (
            f"- {p.title} [child of: {p.parent}] ({len(p.files or [])} files)"
            if p.parent is not None
            else f"- {p.title} ({len(p.files or [])} files)"
        )
        for p in wiki_plan.pages
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
        system = _SYSTEM + get_language_instruction(wiki_language)
        last_output = (await llm.generate(current_prompt, system=system)) or ""
        last_output = sanitize_mermaid(last_output)
        if validate_mermaid(last_output.strip()):
            return last_output.strip()
    logger.warning(
        "synthesize_diagrams: all %d attempts exhausted for repo %r",
        max_retries,
        repo_name,
    )
    return None
