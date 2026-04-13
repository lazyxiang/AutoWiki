from unittest.mock import AsyncMock

from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.fact_check import (
    FactCheckIssue,
    parse_fact_check_result,
    run_fact_check,
    run_targeted_revision,
    strip_failed_claim,
    strip_failed_diagram,
)
from worker.pipeline.page_outline import DiagramPlan, PageOutline, SectionPlan


def test_parse_pass_verdict():
    raw = {"verdict": "pass", "issues": []}
    result = parse_fact_check_result(raw)
    assert result.verdict == "pass"
    assert result.issues == []


def test_parse_fail_verdict_with_claim_issue():
    raw = {
        "verdict": "fail",
        "issues": [
            {
                "kind": "claim",
                "claim": "Uses IndexFlatL2",
                "section": "## Architecture",
                "reason": "Actually uses IndexFlatIP",
                "suggested_fix": "Change L2 to IP",
            }
        ],
    }
    result = parse_fact_check_result(raw)
    assert result.verdict == "fail"
    assert len(result.issues) == 1
    assert result.issues[0].kind == "claim"
    assert result.issues[0].claim == "Uses IndexFlatL2"


def test_parse_fail_verdict_with_diagram_issue():
    raw = {
        "verdict": "fail",
        "issues": [
            {
                "kind": "diagram",
                "diagram_index": 0,
                "section": "## Flow",
                "reason": "Wrong arrow direction",
                "suggested_fix": "Reverse the arrow",
            }
        ],
    }
    result = parse_fact_check_result(raw)
    assert result.issues[0].kind == "diagram"
    assert result.issues[0].diagram_index == 0


def test_strip_failed_claim_removes_sentence():
    draft = (
        "## Architecture\n\n"
        "The system uses IndexFlatL2 for similarity search. "
        "It stores vectors in a flat structure.\n"
    )
    result = strip_failed_claim(draft, "uses IndexFlatL2", "Wrong index type")
    assert "IndexFlatL2" not in result
    assert "<!-- removed:" in result
    assert "It stores vectors" in result


def test_strip_failed_claim_no_match_returns_draft():
    draft = "## Architecture\n\nNo matching text here.\n"
    result = strip_failed_claim(draft, "some nonexistent claim", "reason")
    assert result == draft


def test_strip_failed_diagram_removes_mermaid_block():
    draft = (
        "## Flow\n\n"
        "**Diagram: Data flow**\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "*Source: main.py:1-10*\n\n"
        "Some text after.\n"
    )
    result = strip_failed_diagram(
        draft, section="## Flow", diagram_index=0, reason="Wrong flow"
    )
    assert "```mermaid" not in result
    assert "<!-- diagram removed:" in result
    assert "Some text after." in result


async def test_run_fact_check_returns_pass():
    mock_fast_llm = AsyncMock()
    mock_fast_llm.generate_structured.return_value = {
        "verdict": "pass",
        "issues": [],
    }

    outline = PageOutline(
        sections=[
            SectionPlan(
                heading="Overview",
                kind="prose",
                focus="f",
                diagram=DiagramPlan(
                    type="flowchart", purpose="p", source_files=["a.py"]
                ),
            ),
        ],
        key_claims=["claim1", "claim2", "claim3"],
    )
    result = await run_fact_check(
        draft="## Overview\nContent here.",
        outline=outline,
        entity_summaries="entities",
        dep_info=None,
        targeted_chunks="code chunks",
        fast_llm=mock_fast_llm,
    )
    assert result.verdict == "pass"
    assert result.issues == []


async def test_run_fact_check_returns_fail():
    mock_fast_llm = AsyncMock()
    mock_fast_llm.generate_structured.return_value = {
        "verdict": "fail",
        "issues": [
            {
                "kind": "claim",
                "claim": "Wrong claim",
                "section": "## Overview",
                "reason": "Not supported by code",
                "suggested_fix": "Remove claim",
            }
        ],
    }

    outline = PageOutline(
        sections=[
            SectionPlan(
                heading="Overview",
                kind="prose",
                focus="f",
                diagram=DiagramPlan(
                    type="flowchart", purpose="p", source_files=["a.py"]
                ),
            ),
        ],
        key_claims=["Wrong claim", "claim2", "claim3"],
    )
    result = await run_fact_check(
        draft="## Overview\nWrong claim here.",
        outline=outline,
        entity_summaries="entities",
        dep_info=None,
        targeted_chunks="code chunks",
        fast_llm=mock_fast_llm,
    )
    assert result.verdict == "fail"
    assert len(result.issues) == 1


async def test_run_fact_check_fails_open_on_error():
    mock_fast_llm = AsyncMock()
    mock_fast_llm.generate_structured.side_effect = RuntimeError("LLM error")

    outline = PageOutline(
        sections=[
            SectionPlan(
                heading="X",
                kind="prose",
                focus="f",
                diagram=DiagramPlan(type="flowchart", purpose="p", source_files=[]),
            ),
        ],
        key_claims=["a", "b", "c"],
    )
    result = await run_fact_check(
        draft="content",
        outline=outline,
        entity_summaries="",
        dep_info=None,
        targeted_chunks="",
        fast_llm=mock_fast_llm,
    )
    assert result.verdict == "pass"


async def test_run_targeted_revision_fixes_claims():
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = "## Overview\n\nRevised content here."

    issues = [
        FactCheckIssue(
            kind="claim",
            claim="wrong claim",
            section="## Overview",
            reason="Incorrect",
            suggested_fix="Fix it",
        )
    ]
    context = [PromptSegment(text="context", cacheable=True)]

    result = await run_targeted_revision(
        draft="## Overview\n\nOriginal with wrong claim.",
        issues=issues,
        context_segments=context,
        llm=mock_llm,
    )
    assert "Revised content" in result
    mock_llm.generate.assert_called_once()
