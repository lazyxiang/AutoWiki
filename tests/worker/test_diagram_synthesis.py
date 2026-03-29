from worker.pipeline.diagram_synthesis import synthesize_diagrams, validate_mermaid
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan


def test_validate_mermaid_accepts_valid():
    assert validate_mermaid("graph TD\n  A --> B") is True
    assert validate_mermaid("flowchart LR\n  A --> B") is True
    assert validate_mermaid("classDiagram\n  Animal <|-- Dog") is True


def test_validate_mermaid_rejects_invalid():
    assert validate_mermaid("not a diagram") is False
    assert validate_mermaid("") is False


async def test_synthesize_diagrams_returns_mermaid(mock_llm):
    mock_llm.generate.return_value = "graph TD\n  A[API] --> B[Worker]"
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="API", purpose="API endpoints.", files=["api/main.py"]),
            WikiPageSpec(
                title="Worker", purpose="Background jobs.", files=["worker/jobs.py"]
            ),
        ]
    )
    result = await synthesize_diagrams(plan, repo_name="myrepo", llm=mock_llm)
    assert result is not None
    assert "graph" in result.lower() or "flowchart" in result.lower()


async def test_synthesize_diagrams_retries_on_invalid(mock_llm):
    # First call returns invalid, second returns valid
    mock_llm.generate.side_effect = [
        "not valid mermaid",
        "graph TD\n  A --> B",
    ]
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="Core", purpose="Core module.", files=["src/main.py"]),
        ]
    )
    result = await synthesize_diagrams(plan, repo_name="repo", llm=mock_llm)
    assert result is not None
    assert mock_llm.generate.call_count == 2
    # Second call prompt must reference the prior bad output (not snowball)
    second_call_prompt = mock_llm.generate.call_args_list[1][0][0]
    assert "Previous attempt produced invalid Mermaid" in second_call_prompt
    assert "not valid mermaid" in second_call_prompt


async def test_synthesize_diagrams_returns_none_after_max_retries(mock_llm):
    mock_llm.generate.return_value = "not valid"
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="Core", purpose="Core module.", files=[]),
        ]
    )
    result = await synthesize_diagrams(
        plan, repo_name="repo", llm=mock_llm, max_retries=2
    )
    assert result is None
