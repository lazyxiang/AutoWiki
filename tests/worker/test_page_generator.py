import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest

from worker.pipeline.page_generator import (
    PageResult,
    _append_source_files_table,
    _balance_chunks,
    _file_stems,
    _strip_code_blocks,
    _strip_preamble_and_ensure_header,
    compute_generation_order,
    generate_page,
    generate_page_batch,
)
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan


@pytest.fixture
def page_store(tmp_path):
    from worker.pipeline.rag_indexer import FAISSStore

    store = FAISSStore(
        dimension=1536,
        index_path=tmp_path / "idx",
        meta_path=tmp_path / "meta.pkl",
    )
    store.add(
        [np.zeros(1536, dtype=np.float32)],
        [
            {
                "text": "class User: pass",
                "file": "models.py",
                "start_line": 1,
                "end_line": 1,
            }
        ],
    )
    return store


def _make_mock_fast_llm():
    """Mock fast LLM for outline + fact-check passes."""
    m = AsyncMock()
    m.generate_structured.side_effect = [
        # First call: outline
        {
            "sections": [
                {
                    "heading": "Overview",
                    "kind": "prose",
                    "focus": "What it does",
                    "diagram": None,
                },
                {
                    "heading": "Architecture",
                    "kind": "prose+diagram",
                    "focus": "How it works",
                    "diagram": {
                        "type": "flowchart",
                        "purpose": "Component flow",
                        "source_files": ["models.py"],
                    },
                },
            ],
            "key_claims": [
                "User class defines the data model",
                "Models are stored in SQLite",
                "User has an id field",
            ],
        },
        # Second call: fact-check
        {"verdict": "pass", "issues": []},
    ]
    return m


async def test_generate_page_multi_pass(page_store, mock_embedding):
    llm = AsyncMock()
    llm.generate.return_value = (
        "## Overview\n\nThe models module defines data classes.\n\n"
        "## Architecture\n\n"
        "**Diagram: Component flow**\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "*Source: models.py:1-10*\n"
    )
    fast_llm = _make_mock_fast_llm()

    spec = WikiPageSpec(
        title="Models", purpose="Data model classes.", files=["models.py"]
    )
    result = await generate_page(
        spec,
        page_store,
        llm,
        fast_llm,
        mock_embedding,
        repo_name="test",
    )
    assert isinstance(result, PageResult)
    assert result.slug == "models"
    assert len(result.content) > 0
    # Verify both LLMs were called
    assert fast_llm.generate_structured.call_count == 2  # outline + fact-check
    assert llm.generate.call_count == 1  # draft only


async def test_generate_page_with_fact_check_fail_triggers_revision(
    page_store, mock_embedding
):
    llm = AsyncMock()
    fast_llm = AsyncMock()

    fast_llm.generate_structured.side_effect = [
        # Outline
        {
            "sections": [
                {
                    "heading": "Overview",
                    "kind": "prose",
                    "focus": "f",
                    "diagram": {
                        "type": "flowchart",
                        "purpose": "p",
                        "source_files": ["models.py"],
                    },
                },
            ],
            "key_claims": ["claim 1", "claim 2", "claim 3"],
        },
        # Fact-check: fail
        {
            "verdict": "fail",
            "issues": [
                {
                    "kind": "claim",
                    "claim": "claim 1",
                    "section": "## Overview",
                    "reason": "Not supported",
                    "suggested_fix": "Remove it",
                }
            ],
        },
    ]

    _diagram = "```mermaid\nflowchart TD\n  A-->B\n```\n\n*Source: models.py:1-10*"
    llm.generate.side_effect = [
        f"## Overview\n\n**Diagram: Flow**\n\n{_diagram}",
        f"## Overview\n\nRevised content.\n\n**Diagram: Flow**\n\n{_diagram}",
    ]

    spec = WikiPageSpec(title="Models", purpose="Test.", files=["models.py"])
    await generate_page(
        spec,
        page_store,
        llm,
        fast_llm,
        mock_embedding,
        repo_name="test",
    )
    assert llm.generate.call_count == 2  # draft + revision


async def test_generate_page_revision_failure_falls_back_to_strip(
    page_store, mock_embedding
):
    """When revision raises, deterministic fallback strips the flagged claim."""
    llm = AsyncMock()
    fast_llm = AsyncMock()

    fast_llm.generate_structured.side_effect = [
        # Outline — must have ≥1 diagram and 3-8 claims
        {
            "sections": [
                {
                    "heading": "Overview",
                    "kind": "prose+diagram",
                    "focus": "f",
                    "diagram": {
                        "type": "flowchart",
                        "purpose": "Flow",
                        "source_files": ["models.py"],
                    },
                }
            ],
            "key_claims": ["bad claim", "claim b", "claim c"],
        },
        # Fact-check: fail
        {
            "verdict": "fail",
            "issues": [
                {
                    "kind": "claim",
                    "claim": "bad claim",
                    "section": "## Overview",
                    "reason": "Unsupported",
                    "suggested_fix": "Remove",
                }
            ],
        },
    ]

    draft_text = (
        "## Overview\n\nbad claim appears here.\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n*Source: models.py:1-10*"
    )
    llm.generate.side_effect = [
        draft_text,  # draft
        RuntimeError("LLM unavailable"),  # revision fails
    ]

    spec = WikiPageSpec(title="Models", purpose="Test.", files=["models.py"])
    result = await generate_page(
        spec,
        page_store,
        llm,
        fast_llm,
        mock_embedding,
        repo_name="test",
    )
    # Fallback strips the claim text from content
    assert "bad claim" not in result.content


async def test_generate_page_batch_returns_all_results(page_store, mock_embedding):
    """generate_page_batch produces one PageResult per spec."""
    fast_llm = AsyncMock()
    _three_claims = ["claim 1", "claim 2", "claim 3"]
    _diagram_section = {
        "heading": "Overview",
        "kind": "prose+diagram",
        "focus": "f",
        "diagram": {"type": "flowchart", "purpose": "Flow", "source_files": []},
    }
    _outline = {"sections": [_diagram_section], "key_claims": _three_claims}
    _fact_check_pass = {"verdict": "pass", "issues": []}

    # Schema-inspecting callable so concurrent generation is order-independent
    async def _fast_structured(*args, **kwargs):
        schema = kwargs.get("schema", {})
        props = schema.get("properties", {})
        if "verdict" in props:
            return _fact_check_pass
        return _outline

    fast_llm.generate_structured.side_effect = _fast_structured

    llm = AsyncMock()
    _mermaid = "```mermaid\nflowchart TD\n  A-->B\n```\n\n*Source: a.py:1-5*"

    # Return a valid draft regardless of call order
    async def _llm_generate(*args, **kwargs):
        return f"## Overview\n\nContent.\n\n{_mermaid}"

    llm.generate.side_effect = _llm_generate

    from worker.pipeline.ast_analysis import FileAnalysis
    from worker.pipeline.dependency_graph import DependencyGraph

    spec_a = WikiPageSpec(title="Module A", purpose="A module.", files=["a.py"])
    spec_b = WikiPageSpec(title="Module B", purpose="B module.", files=["b.py"])

    results = await generate_page_batch(
        specs_with_children=[(spec_a, None), (spec_b, None)],
        store=page_store,
        llm=llm,
        fast_llm=fast_llm,
        embedding=mock_embedding,
        repo_name="testrepo",
        file_analysis=FileAnalysis(files={}),
        dep_graph=DependencyGraph(),
    )

    assert len(results) == 2
    slugs = {r.slug for r in results}
    assert slugs == {"module-a", "module-b"}
    for r in results:
        assert isinstance(r, PageResult)
        assert len(r.content) > 0


async def test_generate_page_batch_calls_on_result_as_each_page_finishes(
    page_store, mock_embedding, monkeypatch
):
    from worker.pipeline import page_generator
    from worker.pipeline.ast_analysis import FileAnalysis
    from worker.pipeline.dependency_graph import DependencyGraph

    slow_can_finish = asyncio.Event()
    persisted: list[str] = []

    async def fake_generate_page(spec, *args, **kwargs):
        if spec.title == "Slow Page":
            await slow_can_finish.wait()
        return PageResult(slug=spec.slug, title=spec.title, content=spec.title)

    async def on_result(result: PageResult, spec: WikiPageSpec):
        persisted.append(result.title)

    monkeypatch.setattr(page_generator, "generate_page", fake_generate_page)

    spec_fast = WikiPageSpec(title="Fast Page", purpose=".", files=["fast.py"])
    spec_slow = WikiPageSpec(title="Slow Page", purpose=".", files=["slow.py"])
    batch_task = asyncio.create_task(
        generate_page_batch(
            specs_with_children=[(spec_fast, None), (spec_slow, None)],
            store=page_store,
            llm=AsyncMock(),
            fast_llm=AsyncMock(),
            embedding=mock_embedding,
            repo_name="testrepo",
            file_analysis=FileAnalysis(files={}),
            dep_graph=DependencyGraph(),
            on_result=on_result,
        )
    )

    try:
        for _ in range(20):
            if persisted == ["Fast Page"]:
                break
            await asyncio.sleep(0.01)

        assert persisted == ["Fast Page"]
    finally:
        slow_can_finish.set()
        await batch_task


def test_append_source_files_table_adds_table():
    content = "# My Page\n\n## Overview\n\nSome content."
    result = _append_source_files_table(content, ["src/foo.py", "src/bar.py"])
    assert "## Source Files" in result
    assert "| `src/foo.py` |" in result
    assert "| `src/bar.py` |" in result
    assert result.index("## Source Files") > result.index("## Overview")


def test_strip_code_blocks_preserves_mermaid_closing_fence():
    content = "# Page\n\n```mermaid\nflowchart TD\n  A-->B\n```\n\n*Source: a.py:1-2*\n"

    result = _strip_code_blocks(content)

    assert result.count("```") == 2
    assert "```\n\n*Source:" in result


def test_append_source_files_table_empty_files_unchanged():
    content = "# My Page\n\nContent."
    assert _append_source_files_table(content, []) == content


def test_append_source_files_table_at_end():
    content = "# My Page\n\nContent."
    result = _append_source_files_table(content, ["a.py"])
    assert result.endswith("| `a.py` |")


def test_source_files_table_only_includes_passed_files():
    """_append_source_files_table must only include files explicitly passed in."""
    draft = "body text"
    out = _append_source_files_table(draft, ["a.py"])
    assert "a.py" in out
    assert "shared/utils.py" not in out


def test_strip_preamble_removes_reasoning_before_first_heading():
    content = (
        "Let's think about this. I need to revise only the flagged sections.\n\n"
        "Wait, let me check the issue again.\n\n"
        "# My Page\n\n"
        "## Overview\n\nActual content here.\n"
    )
    result = _strip_preamble_and_ensure_header(content, "My Page")
    assert result.startswith("# My Page")
    assert "Let's think" not in result
    assert "Actual content here" in result


def test_strip_preamble_keeps_content_when_no_preamble():
    content = "# My Page\n\n## Overview\n\nContent.\n"
    result = _strip_preamble_and_ensure_header(content, "My Page")
    assert result == content


def test_strip_preamble_adds_title_when_missing():
    content = "## Overview\n\nSome content.\n"
    result = _strip_preamble_and_ensure_header(content, "My Page")
    assert result.startswith("# My Page\n\n## Overview")


def test_strip_preamble_adds_title_after_stripping_preamble():
    content = "Some reasoning text.\n\n## Overview\n\nContent.\n"
    result = _strip_preamble_and_ensure_header(content, "My Page")
    assert result.startswith("# My Page\n\n## Overview")
    assert "reasoning" not in result


def test_strip_preamble_replaces_wrong_h1():
    content = "# Some Other Title\n\n## Overview\n\nContent.\n"
    result = _strip_preamble_and_ensure_header(content, "My Page")
    assert result.startswith("# My Page\n\n## Overview")
    assert "Some Other Title" not in result


def test_compute_generation_order_unchanged():
    """Verify compute_generation_order still works."""
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="Overview", purpose="Root."),
            WikiPageSpec(title="API", purpose="API.", parent="Overview"),
            WikiPageSpec(title="Worker", purpose="Worker.", parent="Overview"),
        ]
    )
    levels = compute_generation_order(plan)
    assert len(levels) == 2
    assert all(p.parent == "Overview" for p in levels[0])
    assert levels[1][0].title == "Overview"


def test_compute_generation_order_single_level():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="A", purpose=".", files=["a.py"]),
            WikiPageSpec(title="B", purpose=".", files=["b.py"]),
        ]
    )
    levels = compute_generation_order(plan)
    assert len(levels) == 1
    assert {p.title for p in levels[0]} == {"A", "B"}


def test_compute_generation_order_two_levels():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="Root", purpose=".", files=["r.py"]),
            WikiPageSpec(title="Child1", purpose=".", parent="Root", files=["c1.py"]),
            WikiPageSpec(title="Child2", purpose=".", parent="Root", files=["c2.py"]),
        ]
    )
    levels = compute_generation_order(plan)
    # Deepest first: children first, then root
    assert len(levels) == 2
    assert {p.title for p in levels[0]} == {"Child1", "Child2"}
    assert {p.title for p in levels[1]} == {"Root"}


def test_compute_generation_order_three_levels():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="Root", purpose=".", files=["r.py"]),
            WikiPageSpec(title="Mid", purpose=".", parent="Root", files=["m.py"]),
            WikiPageSpec(title="Leaf", purpose=".", parent="Mid", files=["l.py"]),
        ]
    )
    levels = compute_generation_order(plan)
    assert len(levels) == 3
    assert levels[0][0].title == "Leaf"
    assert levels[1][0].title == "Mid"
    assert levels[2][0].title == "Root"


def test_compute_generation_order_handles_cycle():
    """Cyclic parent references should not cause infinite recursion."""
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="A", purpose=".", parent="B", files=["a.py"]),
            WikiPageSpec(title="B", purpose=".", parent="A", files=["b.py"]),
        ]
    )
    levels = compute_generation_order(plan)
    # Both pages should appear somewhere (treated as roots due to cycle)
    all_titles = {p.title for level in levels for p in level}
    assert all_titles == {"A", "B"}


# ── A4: multi-query retrieval + rank-weighted chunk quota ────────────────


def _chunk(file: str, idx: int = 0) -> dict:
    """Test helper: minimal RAG chunk dict for _balance_chunks tests."""
    return {
        "file": file,
        "start_line": idx,
        "end_line": idx,
        "text": f"chunk {idx} from {file}",
    }


def test_balance_chunks_rank_weighted_quota():
    """Top-ranked file gets the largest share; every file meets the floor."""
    files = ["a.py", "b.py", "c.py", "d.py", "e.py"]
    chunks = [_chunk(f, i) for f in files for i in range(20)]  # 100 chunks
    out = _balance_chunks(chunks, files=files, k=30, floor=2)

    counts = {f: sum(1 for c in out if c["file"] == f) for f in files}
    # Counts should be non-increasing by rank (top file has the most)
    ordered = [counts[f] for f in files]
    assert ordered == sorted(ordered, reverse=True)
    assert all(counts[f] >= 2 for f in files)
    assert sum(counts.values()) == 30


def test_balance_chunks_floor_only_for_low_rank():
    """Files at rank >= 6 receive only the floor."""
    files = [f"f{i}.py" for i in range(8)]
    chunks = [_chunk(f, i) for f in files for i in range(20)]
    out = _balance_chunks(chunks, files=files, k=30, floor=2)
    counts = {f: sum(1 for c in out if c["file"] == f) for f in files}
    for i in range(6, 8):
        assert counts[f"f{i}.py"] == 2


# ── A5: rank-weighted entity quota ───────────────────────────────────────


def _entity(file: str, name: str) -> dict:
    """Test helper: minimal AST entity dict for _format_entity_details tests."""
    return {
        "type": "function",
        "name": name,
        "signature": f"({name})",
        "file": file,
        "start_line": 10,
        "end_line": 20,
    }


def test_format_entity_details_rank_weighted_per_file_quota():
    """Top-ranked file gets the largest entity share; every file meets the floor."""
    from worker.pipeline.page_formatters import _format_entity_details

    files = ["a.py", "b.py", "c.py"]
    # 20 distinct entities per file so each file has plenty of supply.
    entities = [_entity(f, f"{f}_e{i}") for f in files for i in range(20)]

    out = _format_entity_details(entities, max_entities=15, files=files)

    # Count rendered entities per file by counting the unique entity-name
    # markers — each entity renders one `Location: <file>:` line.
    by_file = {f: out.count(f"Location: {f}:") for f in files}

    # Counts non-increasing by rank — top file has the most.
    assert by_file["a.py"] > by_file["b.py"]
    assert by_file["b.py"] >= by_file["c.py"]
    # Floor: every file gets at least one entity.
    assert all(by_file[f] >= 1 for f in files)
    # Total respects the cap.
    assert sum(by_file.values()) <= 15


def test_format_entity_details_floor_only_for_low_rank():
    """Files at rank >= 6 receive only the floor."""
    from worker.pipeline.page_formatters import _format_entity_details

    files = [f"f{i}.py" for i in range(8)]
    entities = [_entity(f, f"{f}_e{i}") for f in files for i in range(20)]

    out = _format_entity_details(entities, max_entities=30, files=files, floor=2)
    counts = {f: out.count(f"Location: {f}:") for f in files}
    for i in range(6, 8):
        assert counts[f"f{i}.py"] == 2


def test_format_entity_details_without_files_preserves_legacy_behavior():
    """Without `files`, behaviour falls back to the simple cap (back-compat)."""
    from worker.pipeline.page_formatters import _format_entity_details

    entities = [_entity("a.py", f"e{i}") for i in range(50)]
    out = _format_entity_details(entities, max_entities=10)
    # Back-compat: 10 entity bullets rendered.
    assert out.count("- **function**") == 10


def test_file_stems_strips_directory_and_extension():
    """_file_stems returns the bare filename without path or extension."""
    assert _file_stems(["worker/pipeline/foo.py", "src/bar.ts", "baz"]) == [
        "foo",
        "bar",
        "baz",
    ]
