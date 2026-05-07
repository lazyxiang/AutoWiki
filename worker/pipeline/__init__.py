"""Top-level back-compat re-exports for the indexing pipeline.

The pipeline is organised into three sub-packages:

* :mod:`worker.pipeline.retrieval` — chunking + FAISS index + repo search.
* :mod:`worker.pipeline.planner` — wiki plan, outline anchors, user steering.
* :mod:`worker.pipeline.page` — multi-pass page generator (outline / draft /
  fact-check / formatters / diagram post-processor).

This module re-exports the most commonly imported symbols at the top level so
short imports like ``from worker.pipeline import generate_page`` keep working
across the A15 refactor.  Sub-packages remain importable directly when callers
need internal helpers.
"""

from __future__ import annotations

from worker.pipeline.ast_analysis import FileAnalysis
from worker.pipeline.dependency_graph import DependencyGraph
from worker.pipeline.page.generator import (
    PageResult,
    compute_generation_order,
    generate_page,
    generate_page_batch,
)
from worker.pipeline.page.outline import (
    DiagramPlan,
    PageOutline,
    SectionPlan,
    validate_outline,
)
from worker.pipeline.planner.wiki_planner import (
    WikiPageSpec,
    WikiPlan,
    generate_wiki_plan,
)

__all__ = [
    "DependencyGraph",
    "DiagramPlan",
    "FileAnalysis",
    "PageOutline",
    "PageResult",
    "SectionPlan",
    "WikiPageSpec",
    "WikiPlan",
    "compute_generation_order",
    "generate_page",
    "generate_page_batch",
    "generate_wiki_plan",
    "validate_outline",
]
