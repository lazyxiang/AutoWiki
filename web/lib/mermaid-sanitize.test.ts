/**
 * Tests for lib/mermaid-sanitize — Mermaid diagram sanitisation.
 *
 * Test cases are derived from real LLM outputs that caused Mermaid parse
 * errors in the browser.
 */

import { describe, it, expect } from "vitest";
import { sanitizeMermaid } from "./mermaid-sanitize";

// ── Node labels ──────────────────────────────────────────────────────

describe("node label quoting", () => {
  it("quotes parentheses inside square brackets", () => {
    // C[MCP Server (stdio)] — '(' parsed as shape token
    expect(sanitizeMermaid("C[MCP Server (stdio)]")).toBe(
      'C["MCP Server (stdio)"]'
    );
  });

  it("quotes slash inside square brackets", () => {
    // A[Claude Desktop / Cursor] — '/' parsed as parallelogram
    expect(sanitizeMermaid("A[Claude Desktop / Cursor]")).toBe(
      'A["Claude Desktop / Cursor"]'
    );
  });

  it("leaves clean labels unchanged", () => {
    expect(sanitizeMermaid("B[Web Browser]")).toBe("B[Web Browser]");
  });

  it("leaves already-quoted labels unchanged", () => {
    expect(sanitizeMermaid('B["Already quoted"]')).toBe('B["Already quoted"]');
  });

  it("handles multiple nodes on one line", () => {
    const line = "A[Foo (bar)] --> B[Simple] --> C[Baz {x}]";
    const result = sanitizeMermaid(line);
    expect(result).toContain('"Foo (bar)"');
    expect(result).toContain("B[Simple]");
    expect(result).toContain('"Baz {x}"');
  });
});

// ── Edge labels ──────────────────────────────────────────────────────

describe("edge label quoting", () => {
  it("quotes braces in edge label", () => {
    // -->|GET /job-status/{id}| — '{' parsed as diamond-start
    const result = sanitizeMermaid("User -->|GET /job-status/{id}| WebRoutes");
    expect(result).toContain('|"GET /job-status/{id}"|');
  });

  it("quotes slash in edge label", () => {
    const result = sanitizeMermaid("A -->|POST /repo_url| B");
    expect(result).toContain('|"POST /repo_url"|');
  });

  it("leaves clean edge labels unchanged", () => {
    expect(sanitizeMermaid("A -->|Start Job| B")).toBe("A -->|Start Job| B");
  });

  it("leaves already-quoted edge labels unchanged", () => {
    expect(sanitizeMermaid('X -->|"already quoted"| Y')).toBe(
      'X -->|"already quoted"| Y'
    );
  });

  it("quotes parentheses in edge label", () => {
    const result = sanitizeMermaid("A -->|call(foo)| B");
    expect(result).toContain('|"call(foo)"|');
  });

  it("quotes angle brackets in edge label", () => {
    const result = sanitizeMermaid("A -->|List<int>| B");
    expect(result).toContain('|"List<int>"|');
  });
});

// ── Compound shapes ──────────────────────────────────────────────────

describe("compound shapes", () => {
  it("preserves cylinder without special chars", () => {
    expect(sanitizeMermaid("H[(Persistent Output Volume)]")).toBe(
      "H[(Persistent Output Volume)]"
    );
  });

  it("quotes inner text of cylinder with slash", () => {
    expect(sanitizeMermaid("H[(FileSystem /docs)]")).toBe(
      'H[("FileSystem /docs")]'
    );
  });

  it("preserves stadium shape", () => {
    expect(sanitizeMermaid("A([stadium text])")).toBe("A([stadium text])");
  });

  it("preserves double-circle without special chars", () => {
    expect(sanitizeMermaid("A((double circle))")).toBe("A((double circle))");
  });

  it("quotes inner parens in double-circle", () => {
    expect(sanitizeMermaid("A((Server (HTTP)))")).toBe(
      'A(("Server (HTTP)"))'
    );
  });

  it("preserves hexagon without special chars", () => {
    expect(sanitizeMermaid("A{{hexagon text}}")).toBe("A{{hexagon text}}");
  });

  it("quotes special chars in hexagon", () => {
    expect(sanitizeMermaid("A{{call(fn)}}")).toBe('A{{"call(fn)"}}');
  });
});

// ── Full diagram: issue #1 (node labels with parens/slashes) ─────────

describe("full diagram — node label issue", () => {
  const DIAGRAM = [
    "flowchart TD",
    "    subgraph External_Clients",
    "        A[Claude Desktop / Cursor]",
    "        B[Web Browser]",
    "    end",
    "",
    '    subgraph Docker_Container["Docker Container (codewiki)"]',
    "        direction TB",
    "        C[MCP Server (stdio)]",
    "        D[FastAPI Web App]",
    "    end",
    "",
    "    H[(Persistent Output Volume)]",
    "    A <-->|Stdio Transport| C",
    "    G <-->|Mount| I[~/.codewiki/config.json]",
  ].join("\n");

  it("quotes parens in node label", () => {
    expect(sanitizeMermaid(DIAGRAM)).toContain('C["MCP Server (stdio)"]');
  });

  it("quotes slash in node label", () => {
    expect(sanitizeMermaid(DIAGRAM)).toContain('A["Claude Desktop / Cursor"]');
  });

  it("leaves already-quoted subgraph label", () => {
    expect(sanitizeMermaid(DIAGRAM)).toContain(
      'Docker_Container["Docker Container (codewiki)"]'
    );
  });

  it("preserves cylinder shape", () => {
    expect(sanitizeMermaid(DIAGRAM)).toContain("H[(Persistent Output Volume)]");
  });

  it("leaves clean nodes unchanged", () => {
    const result = sanitizeMermaid(DIAGRAM);
    expect(result).toContain("B[Web Browser]");
    expect(result).toContain("D[FastAPI Web App]");
  });

  it("quotes slash in path-like node text", () => {
    expect(sanitizeMermaid(DIAGRAM)).toContain(
      'I["~/.codewiki/config.json"]'
    );
  });
});

// ── Full diagram: issue #2 (edge labels with braces/slashes) ─────────

describe("full diagram — edge label issue", () => {
  const DIAGRAM = [
    "flowchart TD",
    "    User([User Browser]) -->|POST /repo_url| WebRoutes[WebRoutes]",
    "    WebRoutes -->|Start Job| BGWorker[BackgroundWorker]",
    "    BGWorker -->|Updates Status| Cache[CacheManager]",
    "",
    "    User -->|GET /job-status/{id}| WebRoutes",
    "    WebRoutes -->|Query| Cache",
    "",
    "    User -->|GET /static-docs/{id}| WebRoutes",
    "    WebRoutes -->|Read Files| FS[(FileSystem /docs)]",
    "    FS -->|Markdown + JSON| Visualiser[visualise_docs.py]",
    "    Visualiser -->|HTML| User",
  ].join("\n");

  it("quotes braces in edge labels", () => {
    const result = sanitizeMermaid(DIAGRAM);
    expect(result).toContain('|"GET /job-status/{id}"|');
    expect(result).toContain('|"GET /static-docs/{id}"|');
  });

  it("quotes slash in edge label", () => {
    expect(sanitizeMermaid(DIAGRAM)).toContain('|"POST /repo_url"|');
  });

  it("leaves clean edge labels unchanged", () => {
    const result = sanitizeMermaid(DIAGRAM);
    expect(result).toContain("|Start Job|");
    expect(result).toContain("|Query|");
    expect(result).toContain("|HTML|");
  });

  it("quotes inner text of cylinder with slash", () => {
    expect(sanitizeMermaid(DIAGRAM)).toContain('FS[("FileSystem /docs")]');
  });

  it("preserves stadium shape", () => {
    expect(sanitizeMermaid(DIAGRAM)).toContain("User([User Browser])");
  });
});

// ── Edge cases ───────────────────────────────────────────────────────

describe("edge cases", () => {
  it("handles diagram keyword only", () => {
    expect(sanitizeMermaid("flowchart TD")).toBe("flowchart TD");
  });

  it("handles clean diagram unchanged", () => {
    expect(sanitizeMermaid("A[x] --> B[y]")).toBe("A[x] --> B[y]");
  });

  it("handles arrow chain unchanged", () => {
    expect(sanitizeMermaid("A --> B --> C")).toBe("A --> B --> C");
  });

  it("handles subgraph unchanged", () => {
    const input = "subgraph S\n  A --> B\nend";
    expect(sanitizeMermaid(input)).toBe(input);
  });
});
