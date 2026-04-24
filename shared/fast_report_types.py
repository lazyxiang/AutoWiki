from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class FastReportCitation:
    id: str
    file_path: str
    start_line: int
    end_line: int
    label: str
    kind: str
    reason: str = ""
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FastReportEvidenceBlock:
    citation_id: str
    snippet_start: int
    snippet_end: int
    full_start: int
    full_end: int
    default_context: int = 3
    expanded_context: int = 18
    is_collapsed: bool = True
    code: str = ""
    symbol_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FastReportWikiLink:
    slug: str
    title: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FastReportDiagram:
    id: str
    title: str
    type: str
    source: str
    caption: str = ""
    reason: str = ""
    citations: list[str] = field(default_factory=list)
    placement: str = "inline"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
