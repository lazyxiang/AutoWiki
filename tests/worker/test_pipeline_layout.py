"""Layout assertions for the A15 sub-package restructure.

These tests guard the post-A15 module geography:

* Top-level ``worker/pipeline`` keeps only the stage entry-points and
  shared helpers; new modules must land inside ``retrieval/``, ``planner/``,
  or ``page/`` sub-packages.
* The deprecation shims at the old paths remain importable and emit
  ``DeprecationWarning`` so external consumers get a one-release migration
  window.
* Top-level back-compat re-exports keep ``from worker.pipeline import …``
  working for the most commonly imported symbols.
"""

from __future__ import annotations

import importlib
import warnings
from pathlib import Path

import pytest


def test_pipeline_top_level_is_only_stages_and_helpers():
    pipeline_dir = Path(__file__).resolve().parents[2] / "worker" / "pipeline"
    files = sorted(p.name for p in pipeline_dir.iterdir() if p.is_file())
    expected = {
        "__init__.py",
        "language.py",
        "pipeline_logging.py",
        "ingestion.py",
        "ast_analysis.py",
        "dependency_graph.py",
    }
    deprecation_shims = {
        "fast_report_index.py",
        "rag_indexer.py",
        "page_generator.py",
        "page_outline.py",
        "page_draft.py",
        "page_formatters.py",
        "fact_check.py",
        "diagram_post_processor.py",
        "wiki_planner.py",
        "outline_anchors.py",
        "user_steering.py",
    }
    actual = set(files)
    extra = actual - expected - deprecation_shims
    assert not extra, f"unexpected top-level files: {extra}"


def test_back_compat_reexports_resolve():
    from worker.pipeline import (
        DependencyGraph,
        DiagramPlan,
        FileAnalysis,
        PageOutline,
        PageResult,
        SectionPlan,
        WikiPageSpec,
        WikiPlan,
        compute_generation_order,
        generate_page,
        generate_page_batch,
        generate_wiki_plan,
        validate_outline,
    )

    # Spot-check that the symbols are real, not None placeholders.
    assert WikiPlan is not None
    assert WikiPageSpec is not None
    assert FileAnalysis is not None
    assert DependencyGraph is not None
    assert callable(generate_page)
    assert callable(generate_page_batch)
    assert callable(generate_wiki_plan)
    assert callable(compute_generation_order)
    assert callable(validate_outline)
    assert PageOutline is not None
    assert SectionPlan is not None
    assert DiagramPlan is not None
    assert PageResult is not None


@pytest.mark.parametrize(
    "old_module",
    [
        "worker.pipeline.page_generator",
        "worker.pipeline.page_outline",
        "worker.pipeline.page_draft",
        "worker.pipeline.page_formatters",
        "worker.pipeline.fact_check",
        "worker.pipeline.diagram_post_processor",
        "worker.pipeline.wiki_planner",
        "worker.pipeline.outline_anchors",
        "worker.pipeline.user_steering",
        "worker.pipeline.rag_indexer",
    ],
)
def test_old_paths_emit_deprecation_warning(old_module):
    # The shim emits the warning at import time, but the module may already
    # be cached from earlier tests.  Force a fresh import so the warning is
    # observable here.
    import sys

    sys.modules.pop(old_module, None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module(old_module)
    matching = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning) and old_module in str(w.message)
    ]
    assert matching, (
        f"no DeprecationWarning observed for {old_module}: "
        f"{[str(w.message) for w in caught]}"
    )
