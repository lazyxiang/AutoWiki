# worker/pipeline/diagram_post_processor.py
"""Post-processor ensuring every Mermaid diagram block has a header and source."""

from __future__ import annotations

import re

_MERMAID_BLOCK = re.compile(r"```mermaid\n.*?```", re.DOTALL)
_HEADER_PATTERN = re.compile(r"\*\*Diagram:[^\n]*\*\*")
# Match both "*Source: file*" and "*Source:* file" forms to avoid duplicate insertion
_SOURCE_PATTERN = re.compile(r"\*Source:")


def ensure_diagram_headers(
    markdown: str,
    default_source_files: list[str] | None = None,
) -> str:
    """Ensure every mermaid block has a header and source reference.

    For each ```mermaid block:
    - If no **Diagram: ...** header exists in the 3 lines preceding the block,
      inserts **Diagram: Diagram** before it.
    - If no *Source: ...* annotation exists in the 3 lines following the block,
      inserts *Source: <first default_source_file>* after it.

    Processes blocks from end to start so insertions don't shift earlier offsets.
    """
    if not markdown:
        return markdown

    default_source = default_source_files[0] if default_source_files else "unknown"

    matches = list(_MERMAID_BLOCK.finditer(markdown))
    for match in reversed(matches):
        block_start = match.start()
        block_end = match.end()

        # Check for header in the lines immediately before the block
        prefix = markdown[:block_start]
        lines_before = prefix.rstrip().split("\n")
        has_header = any(_HEADER_PATTERN.search(line) for line in lines_before[-3:])

        # Check for source in the 3 lines after the block
        suffix = markdown[block_end:]
        suffix_lines = suffix.lstrip("\n").split("\n")[:3]
        has_source = any(_SOURCE_PATTERN.search(line) for line in suffix_lines)

        # Insert missing source after block (do this first so block_start is unchanged)
        if not has_source:
            source_line = f"\n\n*Source: {default_source}*"
            markdown = markdown[:block_end] + source_line + markdown[block_end:]

        # Insert missing header before block
        if not has_header:
            header_line = "**Diagram: Diagram**\n\n"
            markdown = markdown[:block_start] + header_line + markdown[block_start:]

    return markdown
