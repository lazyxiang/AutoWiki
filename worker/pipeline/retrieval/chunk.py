"""Shared ``Chunk`` dataclass for retrieval modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """A text chunk extracted from a source file.

    Attributes:
        file: Relative path of the source file inside the repository clone.
        text: Raw text content of the chunk.
        line_start: 1-based start line of the chunk within the file.
        line_end: 1-based end line of the chunk within the file (inclusive).
    """

    file: str
    text: str
    line_start: int
    line_end: int
