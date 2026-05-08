from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.page.fact_check import (
    FactCheckIssue,
    parse_fact_check_result,
    run_fact_check,
    run_targeted_revision,
    strip_failed_claim,
    strip_failed_diagram,
)
from worker.pipeline.page.outline import DiagramPlan, PageOutline, SectionPlan


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


def test_parse_drops_claim_issue_without_claim_text():
    """``parse_fact_check_result`` enforces the kind-specific contract.

    JSON Schema ``if``/``then``/``else`` is silently ignored by Anthropic's
    structured-output API, so the parser is the contract enforcement point:
    a ``"claim"`` issue with an empty/missing claim is dropped rather than
    flowing into ``strip_failed_claim`` as a no-op.
    """
    raw = {
        "verdict": "fail",
        "issues": [
            {
                "kind": "claim",
                "section": "## Architecture",
                "reason": "no claim provided",
                "suggested_fix": "fix it",
            },
            {
                "kind": "claim",
                "claim": "valid claim",
                "section": "## Architecture",
                "reason": "fine",
                "suggested_fix": "fix",
            },
        ],
    }
    result = parse_fact_check_result(raw)
    assert len(result.issues) == 1
    assert result.issues[0].claim == "valid claim"


def test_parse_drops_diagram_issue_without_index():
    raw = {
        "verdict": "fail",
        "issues": [
            {
                "kind": "diagram",
                "section": "## Flow",
                "reason": "no index",
                "suggested_fix": "fix",
            },
            {
                "kind": "diagram",
                "diagram_index": -1,
                "section": "## Flow",
                "reason": "negative index",
                "suggested_fix": "fix",
            },
            {
                "kind": "diagram",
                "diagram_index": 2,
                "section": "## Flow",
                "reason": "ok",
                "suggested_fix": "fix",
            },
        ],
    }
    result = parse_fact_check_result(raw)
    assert len(result.issues) == 1
    assert result.issues[0].diagram_index == 2


def test_parse_drops_unknown_kind():
    raw = {
        "verdict": "fail",
        "issues": [
            {
                "kind": "mystery",
                "section": "## X",
                "reason": "r",
                "suggested_fix": "f",
            }
        ],
    }
    assert parse_fact_check_result(raw).issues == []


def test_strip_failed_claim_neutralizes_html_comment_terminator():
    """``reason`` from the LLM must not be able to close the HTML comment.

    A ``-->`` in the reason would otherwise terminate
    ``<!-- removed: ... -->`` early, leaking the trailing text into the
    rendered Markdown.
    """
    draft = "## X\n\nFoo bar baz.\n"
    out = strip_failed_claim(draft, "Foo bar baz", "broken --> attack <script>")
    # The injected terminator must be neutralised; no second ``-->`` may appear
    # before the legitimate one that closes the comment we wrote.
    body = out.split("<!-- removed:", 1)[1]
    closing = body.index("-->")
    assert "-->" not in body[:closing]


def test_strip_failed_diagram_neutralizes_html_comment_terminator():
    draft = "## Flow\n\n```mermaid\nflowchart TD\n  A-->B\n```\n"
    out = strip_failed_diagram(
        draft, section="## Flow", diagram_index=0, reason="-- xss --> bad"
    )
    body = out.split("<!-- diagram removed:", 1)[1]
    closing = body.index("-->")
    assert "-->" not in body[:closing]


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


async def test_run_fact_check_returns_pass(mock_fast_llm):
    mock_fast_llm.generate_structured.side_effect = None
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


async def test_run_fact_check_returns_fail(mock_fast_llm):
    mock_fast_llm.generate_structured.side_effect = None
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


async def test_run_fact_check_fails_open_on_error(mock_fast_llm):
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


async def test_fact_check_logs_failure_with_context(caplog, mock_fast_llm):
    import logging

    from worker.pipeline.page.fact_check import run_fact_check
    from worker.pipeline.page.outline import PageOutline, SectionPlan

    mock_fast_llm.generate_structured.side_effect = RuntimeError("boom")

    outline = PageOutline(
        sections=[SectionPlan(heading="Intro", kind="prose", focus="...")],
        key_claims=["c1", "c2"],
    )

    with caplog.at_level(logging.WARNING, logger="worker.fact_check"):
        result = await run_fact_check(
            draft="draft text",
            outline=outline,
            entity_summaries="",
            dep_info=None,
            targeted_chunks="",
            fast_llm=mock_fast_llm,
        )

    assert result.verdict == "pass"  # fail-open
    failure_logs = [r for r in caplog.records if "fact_check" in r.getMessage()]
    assert any("boom" in r.getMessage() for r in failure_logs)


async def test_factcheck_fails_when_draft_contains_out_of_scope_claim(mock_fast_llm):
    """out_of_scope_claims hit causes early-exit with verdict='fail'; LLM not called."""
    mock_fast_llm.generate_structured.side_effect = None
    mock_fast_llm.generate_structured.return_value = {"verdict": "pass", "issues": []}

    outline = PageOutline(
        sections=[SectionPlan(heading="X", kind="prose", focus="Y", diagram=None)],
        key_claims=["k1", "k2", "k3"],
        out_of_scope_claims=["validates outline JSON"],
    )
    draft = "This module validates outline JSON before drafting."
    result = await run_fact_check(
        draft=draft,
        outline=outline,
        entity_summaries="",
        dep_info=None,
        targeted_chunks="",
        fast_llm=mock_fast_llm,
    )
    assert result.verdict == "fail"
    assert any("out of scope" in i.reason.lower() for i in result.issues)
    assert any(i.kind == "claim" for i in result.issues)
    # LLM should NOT have been consulted — early return
    mock_fast_llm.generate_structured.assert_not_called()


async def test_factcheck_no_oos_hits_calls_llm(mock_fast_llm):
    """When no out-of-scope claim matches the draft, the LLM is still consulted."""
    mock_fast_llm.generate_structured.side_effect = None
    mock_fast_llm.generate_structured.return_value = {"verdict": "pass", "issues": []}

    outline = PageOutline(
        sections=[SectionPlan(heading="X", kind="prose", focus="Y", diagram=None)],
        key_claims=["k1", "k2", "k3"],
        out_of_scope_claims=["some unrelated topic not in draft"],
    )
    draft = "This module does something completely different."
    result = await run_fact_check(
        draft=draft,
        outline=outline,
        entity_summaries="",
        dep_info=None,
        targeted_chunks="",
        fast_llm=mock_fast_llm,
    )
    assert result.verdict == "pass"
    mock_fast_llm.generate_structured.assert_called_once()


async def test_run_targeted_revision_fixes_claims(mock_llm):
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


async def test_run_targeted_revision_fixes_diagram(mock_llm):
    from worker.pipeline.page.fact_check import FactCheckIssue, run_targeted_revision

    mock_llm.generate.return_value = "```mermaid\nflowchart TD\n  A-->C\n```"

    draft = (
        "## Flow\n\n"
        "**Diagram: Data flow**\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "*Source: main.py:1-10*\n\n"
        "Some text after.\n"
    )
    issues = [
        FactCheckIssue(
            kind="diagram",
            diagram_index=0,
            section="## Flow",
            reason="Wrong direction",
            suggested_fix="Change B to C",
        )
    ]
    context = [PromptSegment(text="context", cacheable=True)]

    result = await run_targeted_revision(
        draft=draft,
        issues=issues,
        context_segments=context,
        llm=mock_llm,
    )
    # Original diagram should be replaced
    assert "A-->B" not in result
    assert "A-->C" in result
    # Text after should be preserved
    assert "Some text after." in result
    mock_llm.generate.assert_called_once()
