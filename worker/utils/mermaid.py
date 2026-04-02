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

    # Strip markdown code fences that LLMs sometimes add
    text = re.sub(r"^```(?:mermaid)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text)

    lines = text.split("\n")
    result: list[str] = []

    for line in lines:
        # Sanitise edge labels first (|...|)
        line = _EDGE_LABEL_RE.sub(_edge_replacer, line)
        # Handle double-bracket shapes before single-bracket ones
        line = _DOUBLE_ROUND_RE.sub(_double_bracket_replacer, line)
        line = _DOUBLE_CURLY_RE.sub(_double_bracket_replacer, line)
        # Single-bracket shapes (negative lookahead prevents double-match)
        line = _SQUARE_RE.sub(_node_replacer, line)
        line = _ROUND_RE.sub(_node_replacer, line)
        line = _CURLY_RE.sub(_node_replacer, line)
        result.append(line)

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

    def _replace_block(m: re.Match) -> str:
        fence_open = m.group(1)  # ```mermaid
        body = m.group(2)
        fence_close = m.group(3)  # ```

        sanitized_body = sanitize_mermaid(body)
        # sanitize_mermaid strips fences, so re-wrap
        return f"{fence_open}\n{sanitized_body}\n{fence_close}"

    return re.sub(
        r"(```mermaid)\s*\n(.*?)\n(```)",
        _replace_block,
        markdown,
        flags=re.DOTALL,
    )
