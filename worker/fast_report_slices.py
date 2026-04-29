"""Source slice extraction for fast reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_HASH_COMMENT_EXTENSIONS = {"py", "rb", "sh", "bash", "zsh"}


@dataclass(frozen=True, slots=True)
class SliceResult:
    snippet_start: int
    snippet_end: int
    full_start: int
    full_end: int
    code: str
    truncated_lines: int


def extract_source_slice(
    *,
    clone_root: Path,
    rel_path: str,
    anchor_start: int,
    anchor_end: int,
    line_cap: int,
    context_lines: int = 5,
) -> SliceResult | None:
    file_path = clone_root / rel_path
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, IsADirectoryError, OSError):
        return None

    lines = text.splitlines()
    file_len = len(lines)
    if file_len == 0:
        return SliceResult(
            snippet_start=1,
            snippet_end=1,
            full_start=1,
            full_end=1,
            code="",
            truncated_lines=0,
        )

    anchor_start = max(1, min(int(anchor_start), file_len))
    anchor_end = max(anchor_start, min(int(anchor_end), file_len))
    line_cap = max(1, int(line_cap))

    snippet_end = min(anchor_end, anchor_start + line_cap - 1)
    truncated_lines = max(0, anchor_end - snippet_end)
    code = "\n".join(lines[anchor_start - 1 : snippet_end])
    if truncated_lines:
        code = (
            f"{code}\n{_comment_token(rel_path)} ... "
            f"{truncated_lines} more lines truncated"
        )

    full_start = max(1, anchor_start - context_lines)
    full_end = min(file_len, snippet_end + context_lines)
    return SliceResult(
        snippet_start=anchor_start,
        snippet_end=snippet_end,
        full_start=full_start,
        full_end=full_end,
        code=code,
        truncated_lines=truncated_lines,
    )


def _comment_token(rel_path: str) -> str:
    extension = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
    if extension in _HASH_COMMENT_EXTENSIONS:
        return "#"
    return "//"
