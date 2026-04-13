from worker.pipeline.diagram_post_processor import ensure_diagram_headers


def test_compliant_block_untouched():
    md = (
        "## Section\n\n"
        "**Diagram: Data flow**\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "*Source: main.py:1-10*\n"
    )
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert result == md


def test_missing_header_inserted():
    md = (
        "## Section\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "*Source: main.py:1-10*\n"
    )
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert "**Diagram:" in result
    assert "```mermaid" in result


def test_missing_source_inserted():
    md = "## Section\n\n**Diagram: Flow**\n\n```mermaid\nflowchart TD\n  A-->B\n```\n"
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert "*Source:" in result


def test_missing_both_inserted():
    md = "## Section\n\n```mermaid\nflowchart TD\n  A-->B\n```\n"
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert "**Diagram:" in result
    assert "*Source:" in result


def test_multiple_blocks_handled():
    md = (
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "Some text.\n\n"
        "```mermaid\nsequenceDiagram\n  A->>B: call\n```\n"
    )
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert result.count("**Diagram:") == 2
    assert result.count("*Source:") == 2


def test_no_mermaid_blocks_returns_unchanged():
    md = "## Section\n\nJust text, no diagrams.\n"
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert result == md


def test_missing_source_no_defaults_uses_unknown():
    md = "```mermaid\nflowchart TD\n  A-->B\n```\n"
    result = ensure_diagram_headers(md, default_source_files=None)
    assert "*Source: unknown*" in result
