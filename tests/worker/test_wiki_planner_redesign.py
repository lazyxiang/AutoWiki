import pytest
from unittest.mock import AsyncMock, MagicMock

from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
from worker.pipeline.dependency_graph import DependencyGraph
from worker.pipeline.wiki_planner import (
    WikiPageSpec,
    WikiPlan,
    generate_wiki_plan,
)

def _make_pipeline_analysis():
    """Simulate a multi-stage pipeline structure."""
    files = {
        "worker/pipeline/ingestion.py": FileInfo(
            rel_path="worker/pipeline/ingestion.py",
            entities=[],
            summary="Stage 1: Repo ingestion.",
        ),
        "worker/pipeline/ast_analysis.py": FileInfo(
            rel_path="worker/pipeline/ast_analysis.py",
            entities=[],
            summary="Stage 2: AST analysis.",
        ),
        "worker/pipeline/dependency_graph.py": FileInfo(
            rel_path="worker/pipeline/dependency_graph.py",
            entities=[],
            summary="Stage 3: Dependency graph.",
        ),
        "worker/pipeline/rag_indexer.py": FileInfo(
            rel_path="worker/pipeline/rag_indexer.py",
            entities=[],
            summary="Stage 4: RAG indexing.",
        ),
        "worker/pipeline/wiki_planner.py": FileInfo(
            rel_path="worker/pipeline/wiki_planner.py",
            entities=[],
            summary="Stage 5: Wiki planning.",
        ),
        "worker/pipeline/page_generator.py": FileInfo(
            rel_path="worker/pipeline/page_generator.py",
            entities=[],
            summary="Stage 6: Page generation.",
        ),
        "main.py": FileInfo(
            rel_path="main.py",
            entities=[],
            summary="Entry point.",
        ),
    }
    return FileAnalysis(files=files)

def _make_pipeline_graph():
    """Simulate sequential dependencies for the pipeline."""
    graph = DependencyGraph()
    # ingestion -> ast_analysis -> dependency_graph -> ...
    graph.edges = {
        "worker/pipeline/ingestion.py": ["worker/pipeline/ast_analysis.py"],
        "worker/pipeline/ast_analysis.py": ["worker/pipeline/dependency_graph.py"],
        "worker/pipeline/dependency_graph.py": ["worker/pipeline/rag_indexer.py"],
        "worker/pipeline/rag_indexer.py": ["worker/pipeline/wiki_planner.py"],
        "worker/pipeline/wiki_planner.py": ["worker/pipeline/page_generator.py"],
    }
    graph.clusters = [list(graph.edges.keys()) + ["worker/pipeline/page_generator.py"]]
    return graph

async def test_wiki_planner_pipeline_grouping(mock_llm):
    """
    Verify that the planner groups pipeline stages under a parent page.
    This test ensures Phase 1 (Outline) and Phase 2 (Assignment) work together.
    """
    file_analysis = _make_pipeline_analysis()
    dep_graph = _make_pipeline_graph()
    
    # We want to see if the LLM respects the "Sequential Flow Identification" guideline.
    # We mock the LLM to return a structure that follows this guideline.
    mock_llm.generate_structured.side_effect = [
        # Phase 1: Outline
        {
            "pages": [
                {"title": "Overview", "purpose": "High-level overview."},
                {"title": "Pipeline Overview", "purpose": "The 6-stage wiki generation process."},
                {"title": "Ingestion", "purpose": "Stage 1.", "parent": "Pipeline Overview"},
                {"title": "Analysis & Graph", "purpose": "Stage 2 & 3.", "parent": "Pipeline Overview"},
                {"title": "Indexing & Planning", "purpose": "Stage 4 & 5.", "parent": "Pipeline Overview"},
                {"title": "Page Generation", "purpose": "Stage 6.", "parent": "Pipeline Overview"},
            ]
        },
        # Phase 2: Assignment
        {
            "assignments": [
                {"file": "main.py", "primary_page": "Overview"},
                {"file": "worker/pipeline/ingestion.py", "primary_page": "Ingestion"},
                {"file": "worker/pipeline/ast_analysis.py", "primary_page": "Analysis & Graph", "reference_pages": ["Pipeline Overview"]},
                {"file": "worker/pipeline/dependency_graph.py", "primary_page": "Analysis & Graph", "reference_pages": ["Pipeline Overview"]},
                {"file": "worker/pipeline/rag_indexer.py", "primary_page": "Indexing & Planning"},
                {"file": "worker/pipeline/wiki_planner.py", "primary_page": "Indexing & Planning"},
                {"file": "worker/pipeline/page_generator.py", "primary_page": "Page Generation"},
            ]
        }
    ]
    
    plan = await generate_wiki_plan(
        file_analysis, 
        repo_name="autowiki", 
        llm=mock_llm, 
        dep_graph=dep_graph
    )
    
    assert isinstance(plan, WikiPlan)
    
    # Verify grouping
    pipeline_hub = next(p for p in plan.pages if p.title == "Pipeline Overview")
    assert pipeline_hub.parent is None
    
    children = [p for p in plan.pages if p.parent == "Pipeline Overview"]
    assert len(children) == 4
    child_titles = {c.title for c in children}
    assert "Ingestion" in child_titles
    assert "Analysis & Graph" in child_titles
    
    # Verify file assignments
    analysis_page = next(p for p in plan.pages if p.title == "Analysis & Graph")
    assert "worker/pipeline/ast_analysis.py" in analysis_page.primary_files
    assert "worker/pipeline/dependency_graph.py" in analysis_page.primary_files
    assert analysis_page.parent == "Pipeline Overview"
    
    # Verify reference files (Phase 2 context reuse rule)
    # Note: In the current implementation, we just store reference files list.
    # Page generator uses these to build context.
    assert "worker/pipeline/ast_analysis.py" in pipeline_hub.reference_files
    assert "worker/pipeline/dependency_graph.py" in pipeline_hub.reference_files

async def test_wiki_planner_primary_vs_reference_assignment(mock_llm):
    """
    Verify that files can be primary in one page and reference in another.
    """
    file_analysis = FileAnalysis(files={
        "utils.py": FileInfo(rel_path="utils.py", entities=[], summary="Utility functions."),
        "api.py": FileInfo(rel_path="api.py", entities=[], summary="API logic."),
        "db.py": FileInfo(rel_path="db.py", entities=[], summary="Database logic."),
    })
    
    mock_llm.generate_structured.side_effect = [
        # Phase 1: Outline
        {
            "pages": [
                {"title": "API Layer", "purpose": "Handles requests."},
                {"title": "Data Layer", "purpose": "Handles DB."},
                {"title": "Utilities", "purpose": "Common helpers."},
            ]
        },
        # Phase 2: Assignment
        {
            "assignments": [
                {"file": "utils.py", "primary_page": "Utilities", "reference_pages": ["API Layer", "Data Layer"]},
                {"file": "api.py", "primary_page": "API Layer"},
                {"file": "db.py", "primary_page": "Data Layer"},
            ]
        }
    ]
    
    plan = await generate_wiki_plan(file_analysis, repo_name="test", llm=mock_llm)
    
    utils_page = next(p for p in plan.pages if p.title == "Utilities")
    api_page = next(p for p in plan.pages if p.title == "API Layer")
    data_page = next(p for p in plan.pages if p.title == "Data Layer")
    
    assert "utils.py" in utils_page.primary_files
    assert "utils.py" in api_page.reference_files
    assert "utils.py" in data_page.reference_files
    
    assert "api.py" in api_page.primary_files
    assert "db.py" in data_page.primary_files
