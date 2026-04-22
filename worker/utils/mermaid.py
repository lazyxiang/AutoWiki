"""Mermaid diagram sanitisation utilities.

LLMs frequently produce Mermaid syntax that contains characters with
special meaning in unexpected positions.  Two main categories:

1. **Node labels** — e.g. ``C[MCP Server (stdio)]`` where ``(stdio)``
   is parsed as a shape token.  Fix: ``C["MCP Server (stdio)"]``.

2. **Edge labels** — e.g. ``-->|GET /status/{id}|`` where ``{id}``
   is parsed as a diamond-start token.  Fix: ``-->|"GET /status/{id}"|``.

This module provides :func:`sanitize_mermaid` which post-processes raw
Mermaid text returned by an LLM, quoting any labels that contain
problematic characters while leaving already-quoted labels and valid
compound shapes untouched.
"""

from __future__ import annotations

import re

# Characters that are syntactically meaningful inside Mermaid labels.
_SPECIAL_CHARS = set("(){}|<>/")

# ── Node label patterns ──────────────────────────────────────────────
# One regex per bracket type.  Negative lookahead prevents single-bracket
# patterns from matching double-bracket compound shapes like (( )) / {{ }}.
_SQUARE_RE = re.compile(r"(\b\w+\[)(?!\[)([^\"\]]+)(\])")
_ROUND_RE = re.compile(r"(\b\w+\()(?!\()([^\"\)]+)(\))")
_CURLY_RE = re.compile(r"(\b\w+\{)(?!\{)([^\"\}]+)(\})")

# Double-bracket compound shapes: (( )) and {{ }}.
# Use greedy [^\"]+ so the regex backtracks to find the correct closing )) / }},
# even when the label itself contains ) or }.
_DOUBLE_ROUND_RE = re.compile(r"(\b\w+\(\()([^\"]+)(\)\))")
_DOUBLE_CURLY_RE = re.compile(r"(\b\w+\{\{)([^\"]+)(\}\})")

# ── Edge label pattern ───────────────────────────────────────────────
# Matches edge labels like  -->|label text|  or  ---|label text|
# Excludes already-quoted labels (label starting with `"`)
_EDGE_LABEL_RE = re.compile(r"(\|)([^\"|][^|]*?)(\|)")
_MERMAID_FENCE_OPEN_RE = re.compile(r"^```mermaid[ \t]*$")
_FENCE_CLOSE_RE = re.compile(r"^```[ \t]*$")
_MARKDOWN_BOUNDARY_RE = re.compile(
    r"^(?:\*Source:|_Source:|#{1,6}\s|\|.+\|$|[-*+]\s+|\d+\.\s+)"
)

# -- Edge-type normalisation -----------------------------------------
# LLMs sometimes emit  --|"label"|  (undirected line) instead of
# -->|"label"|  (directed arrow).  Convert  --\|  that is not already
# preceded by  >  or  -  (so  -->|  and  ---|  are left untouched).
_UNDIRECTED_LABELED_EDGE_RE = re.compile(r"(?<![->])--(\|)")

# ── Block-opener patterns by diagram type ────────────────────────────
# Maps the lowercased diagram-type keyword to the regex that matches
# block-opening keywords for that diagram type.
_BLOCK_OPENERS_BY_TYPE: dict[str, re.Pattern[str]] = {
    "flowchart": re.compile(r"^subgraph\b", re.I),
    "graph": re.compile(r"^subgraph\b", re.I),
    "sequencediagram": re.compile(r"^(rect|alt|opt|loop|par|critical|break)\b", re.I),
    # stateDiagram-v2 uses `{ }` braces for composite states — no `end` keyword.
    # Simple `state "Label" as Id` declarations must not be balanced with `end`.
    "statediagram": re.compile(r"a^"),
}
_FALLBACK_BLOCK_OPENER = re.compile(r"^subgraph\b", re.I)


def _is_compound_shape(label: str) -> bool:
    """Return True if *label* is the interior of a compound Mermaid shape.

    Compound shapes nest one bracket pair inside another, e.g.
    ``[(text)]`` (cylinder), ``([text])`` (stadium).
    These should not be quoted because the inner brackets are part of
    the shape syntax — unless the inner text itself contains additional
    special characters.
    """
    if len(label) < 2:
        return False
    pairs = {"(": ")", "[": "]", "{": "}"}
    return label[0] in pairs and pairs[label[0]] == label[-1]


def _inner_needs_quoting(label: str) -> bool:
    """Check if the inner text of a compound shape needs quoting.

    For compound shapes like ``[(text)]``, the inner brackets are syntax.
    Only the text between them can cause problems.
    """
    if len(label) < 3:
        return False
    inner = label[1:-1]
    return bool(_SPECIAL_CHARS & set(inner))


def _needs_quoting(label: str) -> bool:
    """Return True if *label* contains characters that Mermaid would mis-parse."""
    return bool(_SPECIAL_CHARS & set(label))


def _node_replacer(re_match: re.Match) -> str:
    """Regex replacement for node labels inside single-bracket shapes."""
    prefix = re_match.group(1)
    label = re_match.group(2)
    close = re_match.group(3)

    if _is_compound_shape(label):
        # e.g. [(FileSystem /docs)] — inner brackets are shape syntax.
        # Quote the inner text if it has special chars.
        if _inner_needs_quoting(label):
            inner = label[1:-1]
            escaped = inner.replace('"', "#quot;")
            return f'{prefix}{label[0]}"{escaped}"{label[-1]}{close}'
        return re_match.group(0)

    if _needs_quoting(label):
        escaped = label.replace('"', "#quot;")
        return f'{prefix}"{escaped}"{close}'
    return re_match.group(0)


def _double_bracket_replacer(re_match: re.Match) -> str:
    """Regex replacement for double-bracket shapes like (( )) and {{ }}."""
    prefix = re_match.group(1)  # e.g. "A(("
    label = re_match.group(2)  # e.g. "Server (HTTP)"
    close = re_match.group(3)  # e.g. "))"

    if _needs_quoting(label):
        escaped = label.replace('"', "#quot;")
        return f'{prefix}"{escaped}"{close}'
    return re_match.group(0)


def _edge_replacer(re_match: re.Match) -> str:
    """Regex replacement for edge labels inside ``|...|`` delimiters."""
    open_pipe = re_match.group(1)
    label = re_match.group(2)
    close_pipe = re_match.group(3)

    if _SPECIAL_CHARS & set(label):
        escaped = label.replace('"', "#quot;")
        return f'{open_pipe}"{escaped}"{close_pipe}'
    return re_match.group(0)


def _strip_outer_code_fences(text: str) -> str:
    text = re.sub(r"^```(?:mermaid)?\s*\n?", "", text.strip())
    return re.sub(r"\n?```\s*$", "", text)


def _diagram_context(lines: list[str]) -> tuple[str, re.Pattern[str]]:
    for line in lines:
        first = line.strip().lower()
        if not first:
            continue
        for dtype, pattern in _BLOCK_OPENERS_BY_TYPE.items():
            if first.startswith(dtype):
                return dtype, pattern
        break
    return "", _FALLBACK_BLOCK_OPENER


def _strip_embedded_fence(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped.startswith("```"):
        return line, stripped

    remainder = re.sub(r"^```[^|]*\|?\s*", "", stripped)
    if not remainder:
        return None
    return remainder, remainder


def _sanitize_mermaid_line(line: str) -> str:
    line = _UNDIRECTED_LABELED_EDGE_RE.sub(r"-->|", line)
    line = _EDGE_LABEL_RE.sub(_edge_replacer, line)
    line = _DOUBLE_ROUND_RE.sub(_double_bracket_replacer, line)
    line = _DOUBLE_CURLY_RE.sub(_double_bracket_replacer, line)
    line = _SQUARE_RE.sub(_node_replacer, line)
    line = _ROUND_RE.sub(_node_replacer, line)
    return _CURLY_RE.sub(_node_replacer, line)


def _track_block_depth(
    stripped: str,
    diagram_type: str,
    block_opener: re.Pattern[str],
    block_depth: int,
    state_brace_depth: int,
) -> tuple[bool, int, int]:
    if block_opener.match(stripped):
        return True, block_depth + 1, state_brace_depth
    if stripped == "end":
        if block_depth > 0:
            return True, block_depth - 1, state_brace_depth
        return False, block_depth, state_brace_depth
    if diagram_type != "statediagram":
        return True, block_depth, state_brace_depth
    if stripped.startswith("state ") and stripped.endswith("{"):
        return True, block_depth, state_brace_depth + 1
    if stripped == "}":
        if state_brace_depth > 0:
            return True, block_depth, state_brace_depth - 1
        return False, block_depth, state_brace_depth
    return True, block_depth, state_brace_depth


def _looks_like_markdown_boundary(line: str) -> bool:
    return bool(_MARKDOWN_BOUNDARY_RE.match(line.strip()))


def sanitize_mermaid(text: str) -> str:
    """Quote Mermaid node and edge labels that contain special characters.

    Scans each line for:

    - Node definitions like ``A[Label]`` and wraps the label in
      double-quotes when it contains parentheses, pipes, curly braces,
      or angle brackets.
    - Edge labels like ``-->|label|`` and quotes them when they contain
      braces, parentheses, angle brackets, or slashes.
    - Compound shapes like ``[(text)]``, ``([text])``, ``((text))``,
      ``{{text}}`` are preserved; only the inner text is quoted when
      it contains special characters.
    - Orphaned ``end`` keywords (an ``end`` with no matching block-opener
      at the same nesting level) are removed.  The block-opener pattern is
      diagram-type-aware:

      - ``flowchart`` / ``graph`` → ``subgraph``
      - ``sequenceDiagram`` → ``rect``, ``alt``, ``opt``, ``loop``,
        ``par``, ``critical``, ``break``
      - ``stateDiagram`` → ``state``
      - Unrecognised diagram types fall back to ``subgraph``.

    - Unclosed blocks (LLM forgot a closing ``end``) are recovered by
      appending the missing ``end`` lines after the diagram body.

    Already-quoted labels are left unchanged.

    Also strips Markdown code fences if present.

    Args:
        text: Raw Mermaid diagram text, possibly with code fences.

    Returns:
        Sanitised Mermaid text with problematic labels quoted.

    Example::

        >>> sanitize_mermaid('C[MCP Server (stdio)]')
        'C["MCP Server (stdio)"]'
        >>> sanitize_mermaid('A -->|GET /status/{id}| B')
        'A -->|"GET /status/{id}"| B'
        >>> sanitize_mermaid('H[(Persistent Volume)]')
        'H[(Persistent Volume)]'
    """
    if not text:
        return text

    text = _strip_outer_code_fences(text)
    lines = text.split("\n")
    result: list[str] = []
    diagram_type, block_opener = _diagram_context(lines)
    block_depth = 0
    state_brace_depth = 0

    for line in lines:
        normalized = _strip_embedded_fence(line)
        if normalized is None:
            continue

        line, stripped = normalized
        keep_line, block_depth, state_brace_depth = _track_block_depth(
            stripped, diagram_type, block_opener, block_depth, state_brace_depth
        )
        if keep_line:
            result.append(_sanitize_mermaid_line(line))

    result.extend("end" for _ in range(block_depth))
    result.extend("}" for _ in range(state_brace_depth))

    return "\n".join(result)


def sanitize_mermaid_blocks(markdown: str) -> str:
    """Find and sanitise all ```mermaid code blocks within Markdown text.

    Leaves non-mermaid content untouched.

    Args:
        markdown: Full Markdown document that may contain mermaid blocks.

    Returns:
        The same Markdown with mermaid block contents sanitised.
    """
    if not markdown:
        return markdown

    had_trailing_newline = markdown.endswith("\n")
    lines = markdown.splitlines()
    result: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if not _MERMAID_FENCE_OPEN_RE.match(line.strip()):
            result.append(line)
            i += 1
            continue

        fence_open = line
        body: list[str] = []
        i += 1
        found_close = False

        while i < len(lines):
            current = lines[i]
            if _FENCE_CLOSE_RE.match(current.strip()):
                found_close = True
                i += 1
                break
            if _looks_like_markdown_boundary(current):
                break
            body.append(current)
            i += 1

        sanitized_body = sanitize_mermaid("\n".join(body))
        result.append(fence_open)
        if sanitized_body:
            result.extend(sanitized_body.split("\n"))
        result.append("```")

        if found_close:
            continue
        if i < len(lines) and _looks_like_markdown_boundary(lines[i]):
            result.append("")

    output = "\n".join(result)
    if had_trailing_newline:
        output += "\n"
    return output
