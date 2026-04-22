"""Tests for worker.utils.mermaid — Mermaid diagram sanitisation.

Test cases are derived from real LLM outputs that caused Mermaid parse
errors in the browser.
"""

import pytest

from worker.utils.mermaid import sanitize_mermaid, sanitize_mermaid_blocks

# ── Node labels ──────────────────────────────────────────────────────


class TestNodeLabelQuoting:
    """Node labels inside [...], (...), {...} brackets."""

    def test_parens_inside_square_brackets(self):
        """C[MCP Server (stdio)] — '(' parsed as shape token."""
        assert sanitize_mermaid("C[MCP Server (stdio)]") == 'C["MCP Server (stdio)"]'

    def test_slash_inside_square_brackets(self):
        """A[Claude Desktop / Cursor] — '/' parsed as parallelogram."""
        assert (
            sanitize_mermaid("A[Claude Desktop / Cursor]")
            == 'A["Claude Desktop / Cursor"]'
        )

    def test_no_special_chars_unchanged(self):
        assert sanitize_mermaid("B[Web Browser]") == "B[Web Browser]"

    def test_already_quoted_unchanged(self):
        assert sanitize_mermaid('B["Already quoted"]') == 'B["Already quoted"]'

    def test_multiple_nodes_on_line(self):
        line = "A[Foo (bar)] --> B[Simple] --> C[Baz {x}]"
        result = sanitize_mermaid(line)
        assert '"Foo (bar)"' in result
        assert "B[Simple]" in result
        assert '"Baz {x}"' in result


# ── Edge labels ──────────────────────────────────────────────────────


class TestEdgeLabelQuoting:
    """Edge labels inside |...| delimiters."""

    def test_braces_in_edge_label(self):
        """-->|GET /job-status/{id}| — '{' parsed as diamond-start."""
        result = sanitize_mermaid("User -->|GET /job-status/{id}| WebRoutes")
        assert '|"GET /job-status/{id}"|' in result

    def test_slash_in_edge_label(self):
        result = sanitize_mermaid("A -->|POST /repo_url| B")
        assert '|"POST /repo_url"|' in result

    def test_no_special_chars_edge_unchanged(self):
        assert sanitize_mermaid("A -->|Start Job| B") == "A -->|Start Job| B"

    def test_already_quoted_edge_unchanged(self):
        assert (
            sanitize_mermaid('X -->|"already quoted"| Y') == 'X -->|"already quoted"| Y'
        )

    def test_parens_in_edge_label(self):
        result = sanitize_mermaid("A -->|call(foo)| B")
        assert '|"call(foo)"|' in result

    def test_angle_brackets_in_edge_label(self):
        result = sanitize_mermaid("A -->|List<int>| B")
        assert '|"List<int>"|' in result


# ── Compound shapes ──────────────────────────────────────────────────


class TestCompoundShapes:
    """Compound Mermaid shapes that should not be broken."""

    def test_cylinder_no_special_chars(self):
        """[(text)] — cylinder shape preserved."""
        assert sanitize_mermaid("H[(Persistent Output Volume)]") == (
            "H[(Persistent Output Volume)]"
        )

    def test_cylinder_with_slash(self):
        """[(FileSystem /docs)] — inner text with / gets quoted."""
        assert sanitize_mermaid("H[(FileSystem /docs)]") == 'H[("FileSystem /docs")]'

    def test_stadium_shape(self):
        """([text]) — stadium shape preserved."""
        assert sanitize_mermaid("A([stadium text])") == "A([stadium text])"

    def test_double_circle_no_special_chars(self):
        """((text)) — double-circle preserved."""
        assert sanitize_mermaid("A((double circle))") == "A((double circle))"

    def test_double_circle_with_parens(self):
        """((Server (HTTP))) — inner parens quoted."""
        assert sanitize_mermaid("A((Server (HTTP)))") == 'A(("Server (HTTP)"))'

    def test_hexagon_no_special_chars(self):
        """{{text}} — hexagon preserved."""
        assert sanitize_mermaid("A{{hexagon text}}") == "A{{hexagon text}}"

    def test_hexagon_with_special_chars(self):
        assert sanitize_mermaid("A{{call(fn)}}") == 'A{{"call(fn)"}}'


# ── Full diagram: issue #1 (node labels with parens/slashes) ─────────


class TestFullDiagramNodeLabels:
    """Real LLM output that caused 'Syntax error in text: mermaid version 11.13.0'."""

    DIAGRAM = (
        "flowchart TD\n"
        "    subgraph External_Clients\n"
        "        A[Claude Desktop / Cursor]\n"
        "        B[Web Browser]\n"
        "    end\n"
        "\n"
        '    subgraph Docker_Container["Docker Container (codewiki)"]\n'
        "        direction TB\n"
        "        C[MCP Server (stdio)]\n"
        "        D[FastAPI Web App]\n"
        "    end\n"
        "\n"
        "    H[(Persistent Output Volume)]\n"
        "    A <-->|Stdio Transport| C\n"
        "    G <-->|Mount| I[~/.codewiki/config.json]"
    )

    def test_parens_quoted(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert 'C["MCP Server (stdio)"]' in result

    def test_slash_quoted(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert 'A["Claude Desktop / Cursor"]' in result

    def test_already_quoted_subgraph_unchanged(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert 'Docker_Container["Docker Container (codewiki)"]' in result

    def test_cylinder_preserved(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert "H[(Persistent Output Volume)]" in result

    def test_clean_nodes_unchanged(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert "B[Web Browser]" in result
        assert "D[FastAPI Web App]" in result

    def test_slash_in_node_text(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert 'I["~/.codewiki/config.json"]' in result


# ── Full diagram: issue #2 (edge labels with braces/slashes) ─────────


class TestFullDiagramEdgeLabels:
    """Real LLM output where |GET /job-status/{id}| caused diamond-start error."""

    DIAGRAM = (
        "flowchart TD\n"
        "    User([User Browser]) -->|POST /repo_url| WebRoutes[WebRoutes]\n"
        "    WebRoutes -->|Start Job| BGWorker[BackgroundWorker]\n"
        "    BGWorker -->|Updates Status| Cache[CacheManager]\n"
        "\n"
        "    User -->|GET /job-status/{id}| WebRoutes\n"
        "    WebRoutes -->|Query| Cache\n"
        "\n"
        "    User -->|GET /static-docs/{id}| WebRoutes\n"
        "    WebRoutes -->|Read Files| FS[(FileSystem /docs)]\n"
        "    FS -->|Markdown + JSON| Visualiser[visualise_docs.py]\n"
        "    Visualiser -->|HTML| User"
    )

    def test_braces_in_edge_quoted(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert '|"GET /job-status/{id}"|' in result
        assert '|"GET /static-docs/{id}"|' in result

    def test_slash_in_edge_quoted(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert '|"POST /repo_url"|' in result

    def test_clean_edge_unchanged(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert "|Start Job|" in result
        assert "|Query|" in result
        assert "|HTML|" in result

    def test_cylinder_inner_slash_quoted(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert 'FS[("FileSystem /docs")]' in result

    def test_stadium_shape_preserved(self):
        result = sanitize_mermaid(self.DIAGRAM)
        assert "User([User Browser])" in result


# ── Code fences ──────────────────────────────────────────────────────


class TestCodeFenceStripping:
    def test_strips_mermaid_fences(self):
        text = "```mermaid\ngraph TD\n  A --> B\n```"
        assert sanitize_mermaid(text) == "graph TD\n  A --> B"

    def test_strips_plain_fences(self):
        text = "```\ngraph TD\n  A --> B\n```"
        assert sanitize_mermaid(text) == "graph TD\n  A --> B"


# ── sanitize_mermaid_blocks (Markdown-level) ─────────────────────────


class TestSanitizeMermaidBlocks:
    def test_quotes_inside_mermaid_block(self):
        md = (
            "# Title\n\n"
            "Some text.\n\n"
            "```mermaid\n"
            "flowchart TD\n"
            "    A[Server (HTTP)] -->|GET /api/{id}| B[Client]\n"
            "```\n\n"
            "More text."
        )
        result = sanitize_mermaid_blocks(md)
        assert "# Title" in result
        assert "More text." in result
        assert 'A["Server (HTTP)"]' in result
        assert '|"GET /api/{id}"|' in result

    def test_non_mermaid_blocks_unchanged(self):
        md = "```python\nprint('hello (world)')\n```"
        assert sanitize_mermaid_blocks(md) == md

    def test_empty_input(self):
        assert sanitize_mermaid_blocks("") == ""
        assert sanitize_mermaid_blocks(None) is None  # type: ignore[arg-type]

    def test_multiple_mermaid_blocks(self):
        md = "```mermaid\nA[Foo (x)]\n```\ntext\n```mermaid\nB -->|GET /y| C\n```"
        result = sanitize_mermaid_blocks(md)
        assert 'A["Foo (x)"]' in result
        assert '|"GET /y"|' in result

    def test_unclosed_mermaid_block_closes_before_source_annotation(self):
        md = (
            "# Application UI Layer\n\n"
            "**Diagram: UI Navigation and Activity Flow**\n\n"
            "```mermaid\n"
            "flowchart TD\n"
            '    Start(["App Start"]) --> Main["MainActivity (Dashboard)"]\n'
            '    Main -->|"onKeyDown (Back)"| ExitDialog{"Exit Dialog"}\n'
            '    ExitDialog -->|"No"| Main\n'
            "\n"
            "*Source: src/com/seven/network/ericliu/MainActivity.java:71-745*\n\n"
            "## MainActivity Lifecycle and Core Responsibilities\n\n"
            "The lifecycle section must remain Markdown."
        )

        result = sanitize_mermaid_blocks(md)

        assert result.count("```") == 2
        assert "```\n\n*Source:" in result
        assert "## MainActivity Lifecycle" in result
        mermaid_body = result.split("```mermaid\n", 1)[1].split("\n```", 1)[0]
        assert "*Source:" not in mermaid_body
        assert "## MainActivity" not in mermaid_body

    def test_unclosed_mermaid_block_closes_before_next_heading(self):
        md = (
            "```mermaid\n"
            "flowchart TD\n"
            "    A --> B\n"
            "\n"
            "## Next Section\n\n"
            "Text after the diagram."
        )

        result = sanitize_mermaid_blocks(md)

        assert result.count("```") == 2
        assert "```\n\n## Next Section" in result

    def test_unclosed_state_diagram_block_closes_state_before_markdown_fence(self):
        md = (
            "```mermaid\n"
            "stateDiagram-v2\n"
            '    [*] --> Created: "onCreate()"\n'
            "\n"
            "    state Started {\n"
            '        D["User Interaction"] --> E["onActivityResult()"]\n'
            '        D --> F["onKeyDown()"]\n'
            "\n"
            "*Source: src/MainActivity.java:114-153*\n"
        )

        result = sanitize_mermaid_blocks(md)

        mermaid_body = result.split("```mermaid\n", 1)[1].split("\n```", 1)[0]
        assert mermaid_body.rstrip().endswith("}")
        assert "}\n```\n\n*Source:" in result


# ── Edge cases ───────────────────────────────────────────────────────


class TestUndirectedEdgeNormalisation:
    """--|"label"| should be converted to -->|"label"|."""

    def test_undirected_labeled_edge_converted(self):
        result = sanitize_mermaid('E --|"fail"| F')
        assert 'E -->|"fail"| F' == result

    def test_directed_edge_unchanged(self):
        result = sanitize_mermaid('E -->|"fail"| F')
        assert 'E -->|"fail"| F' == result

    def test_triple_dash_unchanged(self):
        result = sanitize_mermaid('A ---|"label"| B')
        assert 'A ---|"label"| B' == result

    def test_unquoted_undirected_edge_converted_and_quoted(self):
        # --|fail (x)| -- undirected edge with special char in label
        result = sanitize_mermaid("E --|fail (x)| F")
        assert '-->|"fail (x)"|' in result

    def test_full_decision_diagram(self):
        diagram = (
            "flowchart TD\n"
            "    D --> E{Issue?}\n"
            '    E --|"fail"| F["Revision"]\n'
            '    E --|"pass"| G\n'
        )
        result = sanitize_mermaid(diagram)
        assert '-->|"fail"|' in result
        assert '-->|"pass"|' in result


class TestEmbeddedCodeFenceRemoval:
    """Lines starting with ``` are stripped of their prefix; content after | is kept."""

    def test_plain_fence_dropped(self):
        diagram = "flowchart TD\n    A --> B\n```\n    B --> C"
        result = sanitize_mermaid(diagram)
        assert "```" not in result

    def test_fence_without_pipe_dropped(self):
        diagram = "flowchart TD\n    A --> B\n```mermaid extra content\n    B --> C\n"
        result = sanitize_mermaid(diagram)
        assert "```" not in result
        assert "A --> B" in result
        assert "B --> C" in result

    def test_fence_with_pipe_keeps_node_definition(self):
        # ```mermaid text| NodeScanner["..."] -- node definition after | is kept.
        diagram = (
            "flowchart TD\n"
            "    A --> B\n"
            '```mermaid text| NodeScanner["label"]\n'
            "    NodeScanner --> C\n"
        )
        result = sanitize_mermaid(diagram)
        assert "```" not in result
        assert 'NodeScanner["label"]' in result
        assert "NodeScanner --> C" in result


class TestSanitizeMermaidBlocksClosingFence:
    """Closing ``` must appear alone at the start of a line.

    The key regression: a line like  ```mermaid text| NodeScanner[...]  starts
    with  ```  but has trailing text, so the old lazy  \\n(```)  pattern closed
    the block there, cutting off the rest of the diagram.  The new pattern
    requires  ^(```)[ \\t]*$  (nothing after the backticks).
    """

    def test_backtick_in_node_label_mid_line_not_premature_close(self):
        # ``` appears INSIDE a node label mid-line, not at line start -- always fine.
        md = (
            "```mermaid\n"
            "flowchart TD\n"
            '    A["detect ```mermaid code block"] --> B\n'
            "    B --> C\n"
            "```\n"
        )
        result = sanitize_mermaid_blocks(md)
        assert "B --> C" in result

    def test_fence_with_trailing_text_not_premature_close(self):
        # ``` at start of a line but with extra text (```mermaid text| ...).
        # Old regex closed the block here; new regex skips it.
        md = (
            "```mermaid\n"
            "flowchart TD\n"
            "    A --> B\n"
            "```mermaid text| extra\n"
            "    B --> C\n"
            "```\n"
        )
        result = sanitize_mermaid_blocks(md)
        assert "A --> B" in result
        assert "B --> C" in result


class TestEdgeCases:
    def test_empty_string(self):
        assert sanitize_mermaid("") == ""

    def test_none_input(self):
        assert sanitize_mermaid(None) is None  # type: ignore[arg-type]

    def test_plain_text_no_crash(self):
        assert sanitize_mermaid("just some text") == "just some text"

    def test_diagram_keyword_only(self):
        assert sanitize_mermaid("flowchart TD") == "flowchart TD"

    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("A[x] --> B[y]", "A[x] --> B[y]"),
            ("A --> B --> C", "A --> B --> C"),
            ("subgraph S\n  A --> B\nend", "subgraph S\n  A --> B\nend"),
        ],
    )
    def test_clean_diagrams_unchanged(self, inp, expected):
        assert sanitize_mermaid(inp) == expected


class TestOrphanedEnd:
    """Orphaned `end` keywords with no matching `subgraph` are removed."""

    def test_orphaned_end_removed(self):
        """LLM uses node definition instead of subgraph syntax; stray `end` dropped."""
        diagram = (
            "flowchart LR\n"
            '    SubGraph1["Session Request"]\n'
            '        GetSess["get_session()"] --> CheckCache["Check cache"]\n'
            "    end\n"
            '    Teardown["dispose_db()"] --> CloseEngine["engine.dispose()"]'
        )
        result = sanitize_mermaid(diagram)
        assert "end" not in [line.strip() for line in result.splitlines()]
        assert 'Teardown["dispose_db()"]' in result

    def test_valid_subgraph_end_preserved(self):
        """A properly opened subgraph keeps its `end` keyword."""
        diagram = (
            "flowchart TD\n    subgraph Init\n        A --> B\n    end\n    B --> C"
        )
        result = sanitize_mermaid(diagram)
        assert result.count("subgraph") == 1
        assert result.count("end") == 1

    def test_nested_subgraphs_balanced(self):
        """Nested subgraphs with matching ends are fully preserved."""
        diagram = (
            "flowchart TD\n"
            "    subgraph Outer\n"
            "        subgraph Inner\n"
            "            A --> B\n"
            "        end\n"
            "    end"
        )
        result = sanitize_mermaid(diagram)
        assert result.count("subgraph") == 2
        assert result.count("end") == 2

    def test_mixed_valid_and_orphaned(self):
        """Valid subgraph + orphaned end: keep valid, drop orphan."""
        diagram = (
            "flowchart LR\n"
            "    subgraph Auth\n"
            "        A --> B\n"
            "    end\n"
            '    Orphan["Node"]\n'
            "    end\n"
            "    B --> C"
        )
        result = sanitize_mermaid(diagram)
        assert result.count("end") == 1
        assert "subgraph Auth" in result


class TestSequenceDiagramEndHandling:
    """sequenceDiagram block-openers (rect/alt/opt/loop/par/critical/break) need end."""

    def test_rect_end_preserved(self):
        """A sequenceDiagram with a single rect...end block keeps the end."""
        diagram = (
            "sequenceDiagram\n"
            "    participant A\n"
            "    participant B\n"
            "    rect rgb(200, 220, 255)\n"
            "        A->>B: Hello\n"
            "    end"
        )
        result = sanitize_mermaid(diagram)
        assert "end" in [line.strip() for line in result.splitlines()]

    def test_alt_else_end_preserved(self):
        """A sequenceDiagram with alt...else...end keeps end and passes else through."""
        diagram = (
            "sequenceDiagram\n"
            "    A->>B: Request\n"
            "    alt success\n"
            "        B->>A: 200 OK\n"
            "    else failure\n"
            "        B->>A: 500 Error\n"
            "    end"
        )
        result = sanitize_mermaid(diagram)
        lines = [line.strip() for line in result.splitlines()]
        assert "end" in lines
        assert any(line.startswith("else") for line in lines)

    def test_unclosed_rect_balanced(self):
        """A sequenceDiagram with an unclosed rect gets an end appended."""
        diagram = (
            "sequenceDiagram\n"
            "    participant A\n"
            "    rect rgb(200, 220, 255)\n"
            "        A->>A: Internal"
        )
        result = sanitize_mermaid(diagram)
        assert result.strip().endswith("end")

    def test_nested_rect_both_ends_preserved(self):
        """Two nested rect blocks in a sequenceDiagram preserve both end lines."""
        diagram = (
            "sequenceDiagram\n"
            "    rect rgb(200, 220, 255)\n"
            "        rect rgb(255, 200, 200)\n"
            "            A->>B: Nested\n"
            "        end\n"
            "    end"
        )
        result = sanitize_mermaid(diagram)
        assert result.count("end") == 2

    def test_flowchart_orphaned_end_still_dropped(self):
        """Regression: a flowchart with a stray end (no subgraph) still drops it."""
        diagram = (
            "flowchart LR\n"
            '    NodeA["Session Request"]\n'
            "        NodeA --> NodeB\n"
            "    end\n"
            '    NodeC["Cleanup"]'
        )
        result = sanitize_mermaid(diagram)
        assert "end" not in [line.strip() for line in result.splitlines()]


class TestStateDiagramEndHandling:
    """stateDiagram-v2 uses braces for composite states, not end keywords."""

    def test_state_declaration_does_not_append_end(self):
        """Simple state declarations must not be balanced with end."""
        diagram = (
            "stateDiagram-v2\n"
            '    state "Pending Review" as Pending\n'
            "    [*] --> Pending"
        )
        assert sanitize_mermaid(diagram) == diagram

    def test_orphaned_end_in_state_diagram_dropped(self):
        """An orphaned end in a stateDiagram (no open block) is dropped."""
        diagram = "stateDiagram-v2\n    [*] --> Active\n    end\n    Active --> [*]"
        result = sanitize_mermaid(diagram)
        assert "end" not in [line.strip() for line in result.splitlines()]
