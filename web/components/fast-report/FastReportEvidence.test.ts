// @vitest-environment jsdom

import React from "react";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  FastReportCitation,
  FastReportEvidenceBlock,
  FastReportSection,
} from "@/lib/api";

import { EvidenceBlock } from "./EvidenceBlock";
import { EvidenceRail } from "./EvidenceRail";
import { ReportSection } from "./ReportSection";

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
    expanded_context: 99,
    is_collapsed: true,
    code: Array.from({ length: 80 }, (_, index) => `line ${index + 1}`).join("\n"),
    symbol_path: "AuthService.validate",
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
      "## Overview\n\n- Requests pass through the guard [cite-auth].\n- Cache usage is separate [cite-cache].\n",
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
      makeEvidenceBlock(),
      makeEvidenceBlock({
        citation_id: "cite-cache",
        snippet_start: 7,
        snippet_end: 10,
        default_context: 3,
        expanded_context: 60,
        code: Array.from({ length: 80 }, (_, index) => `cache ${index + 1}`).join("\n"),
        symbol_path: "Cache.read",
      }),
    ],
    related_wiki_pages: [],
    related_diagrams: [],
    created_at: "2026-04-24T01:00:00Z",
    status: "done",
    ...overrides,
  };
}

describe("fast report evidence interactions", () => {
  const scrollIntoView = vi.fn();

  beforeEach(() => {
    scrollIntoView.mockReset();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("clicking a citation focuses and targets the matching evidence block", async () => {
    const user = userEvent.setup();
    const section = makeSection();

    render(
      React.createElement(
        "div",
        { className: "grid" },
        React.createElement(ReportSection, { section }),
        React.createElement(EvidenceRail, { section }),
      ),
    );

    const citationButton = screen.getByRole("button", {
      name: "Jump to evidence for worker/auth.py:20-24",
    });

    await user.click(citationButton);

    await waitFor(() => {
      const target = document.querySelector('[data-evidence-target="cite-auth"]');
      expect(target?.getAttribute("data-focused")).toBe("true");
      expect(scrollIntoView).toHaveBeenCalledTimes(1);
      expect(scrollIntoView.mock.instances[0]).toBe(target);
    });

    const focusedBlock = document.querySelector(
      '[data-evidence-citation-id="cite-auth"]',
    );
    expect(focusedBlock?.getAttribute("data-focused")).toBe("true");

    const unfocusedBlock = document.querySelector(
      '[data-evidence-citation-id="cite-cache"]',
    );
    expect(unfocusedBlock?.getAttribute("data-focused")).toBe("false");
  });

  it("expands by a fixed 15 lines per click and collapses back through UI interaction", async () => {
    const user = userEvent.setup();
    const block = makeEvidenceBlock({
      expanded_context: 200,
    });

    render(
      React.createElement(EvidenceBlock, {
        citation: makeCitation(),
        block,
      }),
    );

    expect(screen.getByText("line 17")).toBeTruthy();
    expect(screen.getByText("line 25")).toBeTruthy();
    expect(screen.queryByText("line 2")).toBeNull();
    expect(screen.queryByText("line 1")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Expand +15" }));

    const evidence = screen.getByRole("table").closest("div")?.parentElement;
    expect(evidence).toBeTruthy();
    expect(screen.getByText("line 2")).toBeTruthy();
    expect(screen.getByText("line 40")).toBeTruthy();
    expect(screen.queryByText("line 1")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Collapse" }));

    expect(screen.getByText("line 17")).toBeTruthy();
    expect(screen.getByText("line 25")).toBeTruthy();
    expect(screen.queryByText("line 2")).toBeNull();
    expect(screen.queryByText("line 40")).toBeNull();
  });

  it("renders clickable citation controls inside the actual report section UI", () => {
    render(React.createElement(ReportSection, { section: makeSection() }));

    const buttons = screen.getAllByRole("button", { name: /Jump to evidence for/ });
    expect(buttons).toHaveLength(2);
    expect(
      within(buttons[0] as HTMLButtonElement).getByText("[1]"),
    ).toBeTruthy();
    expect(
      within(buttons[1] as HTMLButtonElement).getByText("[2]"),
    ).toBeTruthy();
  });
});
