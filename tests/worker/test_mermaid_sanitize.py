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
            sanitize_mermaid('X -->|"already quoted"| Y')
            == 'X -->|"already quoted"| Y'
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
        assert (
            sanitize_mermaid("H[(FileSystem /docs)]") == 'H[("FileSystem /docs")]'
        )

    def test_stadium_shape(self):
        """([text]) — stadium shape preserved."""
        assert sanitize_mermaid("A([stadium text])") == "A([stadium text])"

    def test_double_circle_no_special_chars(self):
        """((text)) — double-circle preserved."""
        assert sanitize_mermaid("A((double circle))") == "A((double circle))"

    def test_double_circle_with_parens(self):
        """((Server (HTTP))) — inner parens quoted."""
        assert (
            sanitize_mermaid("A((Server (HTTP)))") == 'A(("Server (HTTP)"))'
        )

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
        assert '# Title' in result
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
        md = (
            "```mermaid\nA[Foo (x)]\n```\n"
            "text\n"
            "```mermaid\nB -->|GET /y| C\n```"
        )
        result = sanitize_mermaid_blocks(md)
        assert 'A["Foo (x)"]' in result
        assert '|"GET /y"|' in result


# ── Edge cases ───────────────────────────────────────────────────────


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
