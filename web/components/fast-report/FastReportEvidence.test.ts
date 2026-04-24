import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type {
  FastReportCitation,
  FastReportDiagram,
  FastReportEvidenceBlock,
  FastReportSection,
} from "@/lib/api";

import {
  getVisibleEvidenceLines,
  getVisibleEvidenceRange,
} from "./EvidenceBlock";
import { buildEvidenceRailItems } from "./EvidenceRail";
import { ReportSection, injectCitationLinks } from "./ReportSection";

function makeCitation(
  overrides: Partial<FastReportCitation> = {},
): FastReportCitation {
  return {
    id: "cite-auth",
    file_path: "worker/auth.py",
    start_line: 20,
    end_line: 24,
    label: "Auth guard",
    kind: "code",
    reason: "Entry validation",
    score: 0.98,
    ...overrides,
  };
}

function makeEvidenceBlock(
  overrides: Partial<FastReportEvidenceBlock> = {},
): FastReportEvidenceBlock {
  return {
    citation_id: "cite-auth",
    snippet_start: 20,
    snippet_end: 22,
    full_start: 1,
    full_end: 80,
    default_context: 3,
    expanded_context: 18,
    is_collapsed: true,
    code: Array.from({ length: 80 }, (_, index) => `line ${index + 1}`).join("\n"),
    symbol_path: "AuthService.validate",
    ...overrides,
  };
}

function makeDiagram(
  overrides: Partial<FastReportDiagram> = {},
): FastReportDiagram {
  return {
    id: "diagram-1",
    title: "Auth flow",
    type: "mermaid",
    source: "flowchart TD\nA[Request] --> B[Auth]",
    caption: "Authentication flow",
    reason: "Clarifies request path",
    citations: ["cite-auth"],
    placement: "rail",
    ...overrides,
  };
}

function makeSection(
  overrides: Partial<FastReportSection> = {},
): FastReportSection {
  return {
    id: "section-1",
    report_id: "report-1",
    query: "How is auth enforced?",
    title: "Authentication flow",
    summary: "Maps the validation path.",
    markdown:
      "## Overview\n\n- Requests pass through the guard [cite-auth, cite-cache]\n",
    citations: [
      makeCitation(),
      makeCitation({
        id: "cite-cache",
        file_path: "worker/cache.py",
        start_line: 7,
        end_line: 14,
        label: "Cache lookup",
      }),
    ],
    evidence_blocks: [
      makeEvidenceBlock({ citation_id: "cite-cache", snippet_start: 7, snippet_end: 10 }),
      makeEvidenceBlock(),
    ],
    related_wiki_pages: [],
    related_diagrams: [makeDiagram()],
    created_at: "2026-04-24T01:00:00Z",
    status: "done",
    ...overrides,
  };
}

describe("fast report citation rendering", () => {
  it("rewrites markdown citations into structured link targets without touching code fences", () => {
    const markdown = [
      "Here is the claim [cite-auth].",
      "",
      "```ts",
      "const example = '[cite-auth]';",
      "```",
    ].join("\n");

    const result = injectCitationLinks(markdown, new Set(["cite-auth"]));

    expect(result).toContain("[cite-auth](#evidence-cite-auth)");
    expect(result).toContain("const example = '[cite-auth]';");
  });

  it("renders clickable citation controls inside report markdown", () => {
    const html = renderToStaticMarkup(
      React.createElement(ReportSection, { section: makeSection() }),
    );

    expect(html).toContain('data-citation-id="cite-auth"');
    expect(html).toContain('aria-controls="evidence-cite-auth"');
    expect(html).toContain('data-citation-id="cite-cache"');
    expect(html).toContain("Jump to evidence");
  });
});

describe("buildEvidenceRailItems", () => {
  it("orders evidence by citation appearance and keeps related diagrams", () => {
    const items = buildEvidenceRailItems(
      makeSection({
        citations: [
          makeCitation({ id: "cite-b", file_path: "b.py" }),
          makeCitation({ id: "cite-a", file_path: "a.py" }),
        ],
        evidence_blocks: [
          makeEvidenceBlock({ citation_id: "cite-a" }),
          makeEvidenceBlock({ citation_id: "cite-b" }),
        ],
        related_diagrams: [
          makeDiagram({
            citations: ["cite-b"],
          }),
        ],
      }),
    );

    expect(items.map((item) => item.citation.id)).toEqual(["cite-b", "cite-a"]);
    expect(items[0]?.blocks.map((block) => block.citation_id)).toEqual(["cite-b"]);
    expect(items[1]?.blocks.map((block) => block.citation_id)).toEqual(["cite-a"]);
    expect(items[0]?.diagrams.map((diagram) => diagram.id)).toEqual(["diagram-1"]);
  });
});

describe("evidence expansion", () => {
  it("shows default context first and expands by 15 lines per step", () => {
    const block = makeEvidenceBlock();

    expect(getVisibleEvidenceRange(block, 0)).toEqual({ start: 17, end: 25 });
    expect(getVisibleEvidenceRange(block, 1)).toEqual({ start: 2, end: 40 });
    expect(getVisibleEvidenceRange(block, 2)).toEqual({ start: 1, end: 55 });
  });

  it("returns the currently visible code lines for rendering and collapses by resetting to step 0", () => {
    const block = makeEvidenceBlock();

    const expanded = getVisibleEvidenceLines(block, 1);
    const collapsed = getVisibleEvidenceLines(block, 0);

    expect(expanded[0]).toEqual({ lineNumber: 2, content: "line 2" });
    expect(expanded.at(-1)).toEqual({ lineNumber: 40, content: "line 40" });
    expect(collapsed[0]).toEqual({ lineNumber: 17, content: "line 17" });
    expect(collapsed.at(-1)).toEqual({ lineNumber: 25, content: "line 25" });
  });
});
