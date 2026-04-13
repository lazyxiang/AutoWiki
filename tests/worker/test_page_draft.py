from worker.pipeline.page_draft import (
    DRAFT_SYSTEM,
    build_draft_prompt,
)
from worker.pipeline.page_outline import DiagramPlan, PageOutline, SectionPlan
from worker.pipeline.wiki_planner import WikiPageSpec


def test_draft_system_forbids_code_blocks():
    assert (
        "fenced code block" in DRAFT_SYSTEM.lower()
        or "```python" in DRAFT_SYSTEM
        or "Never embed" in DRAFT_SYSTEM
    )


def test_draft_system_mentions_mermaid_palette():
    for diagram_type in [
        "sequenceDiagram",
        "stateDiagram-v2",
        "erDiagram",
        "journey",
        "mindmap",
    ]:
        assert diagram_type in DRAFT_SYSTEM


def test_draft_system_mentions_source_of_truth():
    assert "source code" in DRAFT_SYSTEM.lower()
    assert (
        "canonical" in DRAFT_SYSTEM.lower() or "source of truth" in DRAFT_SYSTEM.lower()
    )


def test_build_draft_prompt_returns_segments():
    outline = PageOutline(
        sections=[
            SectionPlan(heading="Overview", kind="prose", focus="What it does"),
            SectionPlan(
                heading="Flow",
                kind="prose+diagram",
                focus="Request flow",
                diagram=DiagramPlan(
                    type="sequenceDiagram",
                    purpose="API flow",
                    source_files=["api.py"],
                ),
            ),
        ],
        key_claims=["Claim 1", "Claim 2", "Claim 3"],
    )
    spec = WikiPageSpec(title="API Layer", purpose="HTTP endpoints.", files=["api.py"])
    context_chunks = [
        {"file": "api.py", "start_line": 1, "end_line": 20, "text": "code content"}
    ]
    entity_details = [
        {"type": "function", "name": "list_repos", "file": "api.py", "start_line": 5}
    ]

    segments = build_draft_prompt(
        spec=spec,
        outline=outline,
        context_chunks=context_chunks,
        repo_name="test/repo",
        dep_info=None,
        entity_details=entity_details,
        child_contents=None,
    )

    from worker.llm.prompt_segment import PromptSegment

    assert all(isinstance(s, PromptSegment) for s in segments)
    assert segments[0].cacheable is True

    full_text = "".join(s.text for s in segments)
    assert "sequenceDiagram" in full_text
    assert "API Layer" in full_text
    assert "api.py" in full_text


def test_build_draft_prompt_for_parent_page():
    from worker.pipeline.page_generator import PageResult

    outline = PageOutline(
        sections=[
            SectionPlan(heading="Overview", kind="prose", focus="What it does"),
            SectionPlan(
                heading="Subsystem Map",
                kind="prose+diagram",
                focus="How children relate",
                diagram=DiagramPlan(
                    type="mindmap", purpose="Component map", source_files=[]
                ),
            ),
        ],
        key_claims=["Workers connect via Redis", "API is stateless", "Frontend is SPA"],
    )
    spec = WikiPageSpec(title="Architecture", purpose="Top-level.", files=[])
    child_contents = [
        PageResult(slug="api", title="API Layer", content="## API content"),
        PageResult(slug="worker", title="Worker", content="## Worker content"),
    ]

    segments = build_draft_prompt(
        spec=spec,
        outline=outline,
        context_chunks=[],
        repo_name="test/repo",
        dep_info=None,
        entity_details=None,
        child_contents=child_contents,
    )

    full_text = "".join(s.text for s in segments)
    assert "Child Pages" in full_text
    assert "API Layer" in full_text
    assert "SYNTHESIZE" in full_text
