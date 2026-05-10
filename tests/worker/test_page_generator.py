import asyncio
from unittest.mock import AsyncMock

import pytest

from worker.pipeline.page.generator import (
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
from worker.pipeline.planner.wiki_planner import WikiPageSpec, WikiPlan
from worker.pipeline.retrieval.chunk import Chunk
from worker.pipeline.retrieval.keyword_index import KeywordIndex


@pytest.fixture
def page_store():
    """A KeywordIndex pre-populated with one chunk from models.py."""
    chunks = [
        Chunk(
            file="models.py",
            text="class User: pass",
            line_start=1,
            line_end=1,
        )
    ]
    return KeywordIndex.build(chunks, repo_index={})


def _make_mock_fast_llm():
    """Mock fast LLM for outline + skeleton + fact-check + stitch passes."""
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
    # Pass 2a (skeleton) and Pass 2c (stitch) both use fast_llm.generate
    m.generate.return_value = (
        "# Models\n\n## Overview\n\nContent.\n\n## Architecture\n\nContent.\n"
    )
    return m


async def test_generate_page_multi_pass(page_store):
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
        repo_name="test",
    )
    assert isinstance(result, PageResult)
    assert result.slug == "models"
    assert len(result.content) > 0
    # Verify both LLMs were called
    assert fast_llm.generate_structured.call_count == 2  # outline + fact-check
    # 2 sections in mock outline → 2 per-section llm.generate calls
    assert llm.generate.call_count == 2


async def test_generate_page_with_fact_check_fail_triggers_revision(page_store):
    llm = AsyncMock()
    fast_llm = AsyncMock()

    fast_llm.generate_structured.side_effect = [
        # Outline — 1 section so draft = 1 llm.generate call
        {
            "sections": [
                {
                    "heading": "Overview",
                    "kind": "prose+diagram",
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
    # Pass 2a (skeleton) and Pass 2c (stitch) use fast_llm.generate
    fast_llm.generate.return_value = f"# Models\n\n## Overview\n\n{_diagram}"

    llm.generate.side_effect = [
        f"## Overview\n\n**Diagram: Flow**\n\n{_diagram}",  # section draft
        # revision:
        f"## Overview\n\nRevised content.\n\n**Diagram: Flow**\n\n{_diagram}",
    ]

    spec = WikiPageSpec(title="Models", purpose="Test.", files=["models.py"])
    await generate_page(
        spec,
        page_store,
        llm,
        fast_llm,
        repo_name="test",
    )
    assert llm.generate.call_count == 2  # 1 section draft + 1 revision


async def test_generate_page_revision_failure_falls_back_to_strip(page_store):
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
        draft_text,  # section draft (1 section)
        RuntimeError("LLM unavailable"),  # revision fails
    ]
    # Pass 2a (skeleton) and Pass 2c (stitch) use fast_llm.generate
    fast_llm.generate.return_value = "# Models\n\n## Overview\nContent.\n"

    spec = WikiPageSpec(title="Models", purpose="Test.", files=["models.py"])
    result = await generate_page(
        spec,
        page_store,
        llm,
        fast_llm,
        repo_name="test",
    )
    # Fallback strips the claim text from content
    assert "bad claim" not in result.content


async def test_generate_page_batch_returns_all_results(page_store):
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

    _mermaid = "```mermaid\nflowchart TD\n  A-->B\n```\n\n*Source: a.py:1-5*"

    # Pass 2a (skeleton) and Pass 2c (stitch) use fast_llm.generate
    async def _fast_generate(*args, **kwargs):
        return "# Page\n\n## Overview\n\nSkeleton.\n"

    fast_llm.generate.side_effect = _fast_generate

    llm = AsyncMock()

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
        keyword_index=page_store,
        llm=llm,
        fast_llm=fast_llm,
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
    page_store, monkeypatch
):
    from worker.pipeline.ast_analysis import FileAnalysis
    from worker.pipeline.dependency_graph import DependencyGraph
    from worker.pipeline.page import generator as page_generator

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
            keyword_index=page_store,
            llm=AsyncMock(),
            fast_llm=AsyncMock(),
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


async def test_extract_signature_slices_pulls_top_entities(tmp_path):
    """A6: helper reads source files and returns ≤2 slices × ≤4 lines per file."""
    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
    from worker.pipeline.page.generator import _extract_signature_slices

    src = tmp_path / "module.py"
    src.write_text(
        "import os\n"
        "\n"
        "def main():\n"
        '    """Entry point."""\n'
        "    setup()\n"
        "    run()\n"
        "    cleanup()\n"
        "\n"
        "class Helper:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "        self.y = 2\n"
        "\n"
        "def _internal():\n"
        "    pass\n",
    )

    file_analysis = FileAnalysis(
        files={
            "module.py": FileInfo(
                rel_path="module.py",
                entities=[
                    {
                        "type": "function",
                        "name": "main",
                        "start_line": 3,
                        "end_line": 7,
                    },
                    {
                        "type": "class",
                        "name": "Helper",
                        "start_line": 9,
                        "end_line": 12,
                    },
                    {
                        "type": "function",
                        "name": "_internal",
                        "start_line": 14,
                        "end_line": 15,
                    },
                ],
            )
        }
    )

    spec = WikiPageSpec(title="Module", purpose=".", files=["module.py"])
    slices = await _extract_signature_slices(spec, file_analysis, tmp_path)

    assert "module.py" in slices
    file_slices = slices["module.py"]
    # Cap at 2 entities per file
    assert 1 <= len(file_slices) <= 2
    assert any("def main" in s for s in file_slices)
    assert any(s.startswith("Lines ") for s in file_slices)
    # Each slice capped at 4 lines
    for s in file_slices:
        assert len(s.splitlines()[1:]) <= 4


async def test_generate_page_uses_en_keywords_as_retrieval_query(
    page_store, monkeypatch
):
    """A4: English keywords from the page spec must become a retrieval query."""
    from worker.pipeline.retrieval.keyword_index import KeywordIndex

    llm = AsyncMock()
    llm.generate.return_value = "## Overview\n\nThe page describes the frontend.\n"
    fast_llm = _make_mock_fast_llm()

    # Capture search queries to verify en_keywords are passed
    search_calls: list[list[str]] = []
    original_search = KeywordIndex.search

    def capturing_search(self, queries, **kwargs):
        search_calls.append(list(queries))
        return original_search(self, queries, **kwargs)

    monkeypatch.setattr(KeywordIndex, "search", capturing_search)

    spec = WikiPageSpec(
        title="前端界面",
        purpose="Frontend user interface.",
        files=["models.py"],
        en_keywords=["web", "components", "next"],
    )

    await generate_page(
        spec,
        page_store,
        llm,
        fast_llm,
        repo_name="test",
    )

    # The en_keywords should appear as one of the query strings
    all_queries = [q for call in search_calls for q in call]
    assert any("web" in q and "components" in q for q in all_queries)


async def test_extract_signature_slices_skips_missing_files(tmp_path):
    """Missing/unreadable files are skipped without raising."""
    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
    from worker.pipeline.page.generator import _extract_signature_slices

    file_analysis = FileAnalysis(
        files={
            "missing.py": FileInfo(
                rel_path="missing.py",
                entities=[
                    {
                        "type": "function",
                        "name": "f",
                        "start_line": 1,
                        "end_line": 2,
                    },
                ],
            )
        }
    )
    spec = WikiPageSpec(title="X", purpose=".", files=["missing.py"])
    slices = await _extract_signature_slices(spec, file_analysis, tmp_path)
    assert slices == {}


async def test_generate_page_strips_oos_claim_without_calling_revision(page_store):
    """Out-of-scope claim in draft is stripped without calling the revision LLM.

    The fact-check returns a single out-of-scope issue.  The generator must:
    1. Strip the offending sentence from the draft.
    2. NOT call llm.generate a second time (no revision LLM call).
    """
    llm = AsyncMock()
    fast_llm = AsyncMock()

    oos_phrase = "validates outline JSON"
    draft_with_oos = (
        "## Overview\n\n"
        f"The module {oos_phrase} before drafting.\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n*Source: models.py:1-10*"
    )
    llm.generate.return_value = draft_with_oos
    # Pass 2a (skeleton): returns frame without OOS phrase.
    # Pass 2c (stitch): returns the stitched draft that includes the OOS phrase
    # (as would happen in reality, since stitch concatenates the section body).
    fast_llm.generate.side_effect = [
        "# Models\n\n## Overview\nSkeleton frame.\n",  # skeleton
        draft_with_oos,  # stitch — carries the OOS phrase so early-exit fires
    ]

    fast_llm.generate_structured.side_effect = [
        # Pass 1: outline
        {
            "sections": [
                {
                    "heading": "Overview",
                    "kind": "prose+diagram",
                    "focus": "overview",
                    "diagram": {
                        "type": "flowchart",
                        "purpose": "Flow",
                        "source_files": ["models.py"],
                    },
                }
            ],
            "key_claims": ["claim a", "claim b", "claim c"],
            "out_of_scope_claims": [oos_phrase],
        },
        # Pass 3: fact-check — must NOT be reached because OOS early-exit fires first.
        # Raise to make any accidental call an explicit test failure.
        Exception("fact-check LLM must not be called when only OOS issues exist"),
    ]

    spec = WikiPageSpec(title="Models", purpose="Test.", files=["models.py"])
    result = await generate_page(
        spec,
        page_store,
        llm,
        fast_llm,
        repo_name="test",
    )

    # The out-of-scope phrase must be stripped from prose.
    import re as _re

    prose_only = _re.sub(r"<!--.*?-->", "", result.content, flags=_re.DOTALL)
    assert oos_phrase not in prose_only
    # Draft was generated (llm.generate called once); revision LLM NOT called
    assert llm.generate.call_count == 1
    # Only Pass 1 outline was called; Pass 3 fact-check was short-circuited by OOS gate
    assert fast_llm.generate_structured.call_count == 1


async def test_extract_signature_slices_empty_when_no_repo_root(tmp_path):
    """Without repo_root, helper returns an empty dict (no extraction)."""
    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
    from worker.pipeline.page.generator import _extract_signature_slices

    file_analysis = FileAnalysis(
        files={
            "a.py": FileInfo(
                rel_path="a.py",
                entities=[
                    {
                        "type": "function",
                        "name": "f",
                        "start_line": 1,
                        "end_line": 2,
                    },
                ],
            )
        }
    )
    spec = WikiPageSpec(title="X", purpose=".", files=["a.py"])
    assert await _extract_signature_slices(spec, file_analysis, None) == {}


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


def _chunk(file: str, idx: int = 0) -> Chunk:
    """Test helper: minimal Chunk object for _balance_chunks tests."""
    return Chunk(
        file=file, text=f"chunk {idx} from {file}", line_start=idx, line_end=idx
    )


def test_balance_chunks_rank_weighted_quota():
    """Top-ranked file gets the largest share; every file meets the floor."""
    files = ["a.py", "b.py", "c.py", "d.py", "e.py"]
    chunks = [_chunk(f, i) for f in files for i in range(20)]  # 100 chunks
    out = _balance_chunks(chunks, files=files, k=30, floor=2)

    counts = {f: sum(1 for c in out if c.file == f) for f in files}
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
    counts = {f: sum(1 for c in out if c.file == f) for f in files}
    for i in range(6, 8):
        assert counts[f"f{i}.py"] == 2


def test_balance_chunks_reuses_unfilled_ranked_file_quota():
    """Unused quota from sparse ranked files is reused by other assigned files."""
    files = ["a.py", "b.py", "c.py"]
    chunks = [_chunk("a.py", 1)] + [_chunk("b.py", i) for i in range(20)]

    out = _balance_chunks(chunks, files=files, k=10, floor=1)

    assert len(out) == 10
    assert sum(1 for c in out if c.file == "a.py") == 1
    assert sum(1 for c in out if c.file == "b.py") == 9


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
    from worker.pipeline.page.formatters import _format_entity_details

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
    from worker.pipeline.page.formatters import _format_entity_details

    files = [f"f{i}.py" for i in range(8)]
    entities = [_entity(f, f"{f}_e{i}") for f in files for i in range(20)]

    out = _format_entity_details(entities, max_entities=30, files=files, floor=2)
    counts = {f: out.count(f"Location: {f}:") for f in files}
    for i in range(6, 8):
        assert counts[f"f{i}.py"] == 2


def test_format_entity_details_reuses_unfilled_ranked_file_quota():
    from worker.pipeline.page.formatters import _format_entity_details

    files = ["a.py", "b.py", "c.py"]
    entities = [_entity("a.py", "a0")] + [_entity("b.py", f"b{i}") for i in range(20)]

    out = _format_entity_details(entities, max_entities=10, files=files, floor=1)

    assert out.count("Location: a.py:") == 1
    assert out.count("Location: b.py:") == 9
    assert out.count("- **function**") == 10


def test_format_entity_details_without_files_preserves_legacy_behavior():
    """Without `files`, behaviour falls back to the simple cap (back-compat)."""
    from worker.pipeline.page.formatters import _format_entity_details

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
