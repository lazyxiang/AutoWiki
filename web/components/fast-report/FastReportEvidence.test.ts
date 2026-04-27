// @vitest-environment jsdom

import React from "react";
import {
  cleanup,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  FastReport,
  FastReportCitation,
  FastReportDiagram,
  FastReportEvidenceBlock,
  FastReportSection,
} from "@/lib/api";

const {
  getFastReportMock,
  startFastReportMock,
  replaceMock,
  connectFastReportStreamMock,
} = vi.hoisted(() => ({
  getFastReportMock: vi.fn(),
  startFastReportMock: vi.fn(),
  replaceMock: vi.fn(),
  connectFastReportStreamMock: vi.fn(() => ({ close: vi.fn() })),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getFastReport: getFastReportMock,
    startFastReport: startFastReportMock,
  };
});

vi.mock("@/lib/ws", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ws")>("@/lib/ws");
  return {
    ...actual,
    connectFastReportStream: connectFastReportStreamMock,
  };
});

import { EvidenceBlock } from "./EvidenceBlock";
import { EvidenceRail } from "./EvidenceRail";
import { FastReportWorkspace } from "./FastReportWorkspace";
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
    id: "diagram-auth-cache",
    title: "Auth and cache flow",
    type: "text",
    source: "auth -> cache",
    caption: "Shared diagram",
    reason: "Shared evidence",
    citations: ["cite-auth", "cite-cache"],
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
        code: Array.from({ length: 80 }, (_, index) => `cache ${index + 1}`).join("\n"),
        symbol_path: "Cache.read",
      }),
    ],
    related_wiki_pages: [],
    related_diagrams: [],
    analysis_trace: {},
    created_at: "2026-04-24T01:00:00Z",
    status: "done",
    ...overrides,
  };
}

function makeReport(overrides: Partial<FastReport> = {}): FastReport {
  return {
    id: "report-1",
    repo_id: "repo-1",
    job_id: "job-1",
    commit_sha: "abc123",
    status: "done",
    error: null,
    active_section_id: "section-new",
    created_at: "2026-04-24T00:00:00Z",
    expires_at: "2026-05-01T00:00:00Z",
    sections: [],
    ...overrides,
  };
}

describe("fast report evidence interactions", () => {
  const scrollIntoView = vi.fn();
  let originalScrollIntoView: typeof Element.prototype.scrollIntoView;

  beforeEach(() => {
    cleanup();
    scrollIntoView.mockReset();
    getFastReportMock.mockReset();
    startFastReportMock.mockReset();
    replaceMock.mockReset();
    connectFastReportStreamMock.mockReset();
    connectFastReportStreamMock.mockImplementation(() => ({ close: vi.fn() }));
    originalScrollIntoView = Element.prototype.scrollIntoView;
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: originalScrollIntoView,
    });
  });

  it("clicking a citation from an older section re-targets the workspace evidence rail to that section", async () => {
    const user = userEvent.setup();
    const olderSection = makeSection({
      id: "section-old",
      title: "Older authentication section",
      citations: [
        makeCitation({
          id: "cite-old",
          file_path: "worker/older_auth.py",
          start_line: 30,
          end_line: 34,
        }),
      ],
      evidence_blocks: [
        makeEvidenceBlock({
          citation_id: "cite-old",
          snippet_start: 30,
          snippet_end: 32,
          code: Array.from({ length: 80 }, (_, index) => `older ${index + 1}`).join("\n"),
        }),
      ],
      markdown:
        "## Overview\n\n- Older auth path still matters [cite-old].\n",
      created_at: "2026-04-24T01:00:00Z",
    });
    const activeSection = makeSection({
      id: "section-new",
      title: "Newest active section",
      citations: [
        makeCitation({
          id: "cite-new",
          file_path: "worker/new_auth.py",
          start_line: 50,
          end_line: 55,
        }),
      ],
      evidence_blocks: [
        makeEvidenceBlock({
          citation_id: "cite-new",
          snippet_start: 50,
          snippet_end: 52,
          code: Array.from({ length: 90 }, (_, index) => `new ${index + 1}`).join("\n"),
        }),
      ],
      markdown:
        "## Overview\n\n- Newest auth path is active [cite-new].\n",
      created_at: "2026-04-24T02:00:00Z",
    });

    getFastReportMock.mockResolvedValue(
      makeReport({
        active_section_id: "section-new",
        sections: [olderSection, activeSection],
      }),
    );

    render(
      React.createElement(FastReportWorkspace, {
        owner: "openai",
        repo: "autowiki",
        repoId: "repo-1",
        repoLabel: "openai/autowiki",
        reportId: "report-1",
      }),
    );

    await screen.findByRole("button", {
      name: "Jump to evidence for worker/older_auth.py:30-34",
    });

    expect(
      document.querySelector("[data-evidence-rail-section-id]")?.getAttribute(
        "data-evidence-rail-section-id",
      ),
    ).toBe("section-new");

    await user.click(
      screen.getByRole("button", {
        name: "Jump to evidence for worker/older_auth.py:30-34",
      }),
    );

    await waitFor(() => {
      const rail = document.querySelector("[data-evidence-rail-section-id]");
      const target = document.querySelector('[data-evidence-target="cite-old"]');
      expect(rail?.getAttribute("data-evidence-rail-section-id")).toBe("section-old");
      expect(target?.getAttribute("data-focused")).toBe("true");
      expect(scrollIntoView).toHaveBeenCalled();
      expect(scrollIntoView.mock.instances.at(-1)).toBe(target);
    });
  });

  it("uses backend collapse state and expanded context as the initial evidence view", async () => {
    const user = userEvent.setup();

    render(
      React.createElement(EvidenceBlock, {
        citation: makeCitation(),
        block: makeEvidenceBlock({
          is_collapsed: false,
          expanded_context: 18,
        }),
      }),
    );

    expect(screen.getByText("line 2")).toBeTruthy();
    expect(screen.getByText("line 40")).toBeTruthy();
    expect(screen.queryByText("line 1")).toBeNull();
    expect(screen.getByRole("button", { name: "Collapse" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Expand +15" }));

    expect(screen.getByText("line 1")).toBeTruthy();
    expect(screen.getByText("line 55")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Collapse" }));

    expect(screen.getByText("line 17")).toBeTruthy();
    expect(screen.getByText("line 25")).toBeTruthy();
    expect(screen.queryByText("line 2")).toBeNull();
  });

  it("keeps a valid focus target for later citations supported only by a shared diagram", async () => {
    const section = makeSection({
      evidence_blocks: [
        makeEvidenceBlock(),
      ],
      related_diagrams: [makeDiagram()],
    });
    const diagramOnlyCitationSection = {
      ...section,
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
      ],
    };
    render(
      React.createElement(EvidenceRail, {
        section: diagramOnlyCitationSection,
        focusedCitationId: "cite-cache",
      }),
    );

    await waitFor(() => {
      const sharedTarget = document.querySelector('[data-evidence-target="cite-auth"]');
      expect(sharedTarget?.getAttribute("data-evidence-targets")).toBe(
        "cite-auth,cite-cache",
      );
      expect(sharedTarget?.getAttribute("data-focused")).toBe("true");
      expect(scrollIntoView).toHaveBeenCalled();
      expect(scrollIntoView.mock.instances.at(-1)).toBe(sharedTarget);
    });

    const diagram = document.querySelector(
      '[data-diagram-id="diagram-auth-cache"]',
    );
    expect(screen.getAllByText("Auth and cache flow")).toHaveLength(1);
    expect(diagram?.getAttribute("data-focused")).toBe("true");
  });

  it("keeps aria-controls valid for aliased shared-diagram citations", () => {
    const section = makeSection({
      evidence_blocks: [makeEvidenceBlock()],
      related_diagrams: [makeDiagram()],
    });
    const diagramOnlyCitationSection = {
      ...section,
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
      evidence_blocks: [makeEvidenceBlock()],
    };

    render(
      React.createElement(
        React.Fragment,
        null,
        React.createElement(ReportSection, { section: diagramOnlyCitationSection }),
        React.createElement(EvidenceRail, {
          section: diagramOnlyCitationSection,
          focusedCitationId: "cite-cache",
        }),
      ),
    );

    const citationButton = screen.getByRole("button", {
      name: "Jump to evidence for worker/cache.py:7-14",
    });
    const controlsId = citationButton.getAttribute("aria-controls");

    expect(controlsId).toBe("evidence-cite-cache");
    expect(document.getElementById("evidence-cite-cache")).not.toBeNull();
    expect(
      document
        .getElementById("evidence-cite-cache")
        ?.getAttribute("data-evidence-alias-for"),
    ).toBe("cite-auth");
  });

  it("ReportSection renders AnalysisTracePanel when section has no body but has analysis trace files", () => {
    const section = makeSection({
      markdown: "",
      status: "running",
      analysis_trace: {
        phase: "retrieving_code_evidence",
        files: [{ path: "worker/jobs.py", role: "entrypoint", reason: "found match", status: "selected" }],
      },
    });
    render(React.createElement(ReportSection, { section }));
    expect(screen.getByText("worker/jobs.py")).toBeTruthy();
  });

  it("EvidenceRail renders AnalysisTracePanel when section is null but analysisTrace has files", () => {
    const trace = {
      phase: "retrieving_code_evidence" as const,
      files: [{ path: "worker/jobs.py", role: "entrypoint", reason: "found match", status: "selected" }],
    };
    render(React.createElement(EvidenceRail, { section: null, focusedCitationId: null, analysisTrace: trace }));
    expect(screen.getByText("worker/jobs.py")).toBeTruthy();
  });

  it("dedupes diagrams shared by multiple citations while keeping them focus-aware", () => {
    const section = makeSection({
      related_diagrams: [makeDiagram()],
    });

    const { rerender } = render(
      React.createElement(EvidenceRail, {
        section,
        focusedCitationId: null,
      }),
    );

    expect(screen.getAllByText("Auth and cache flow")).toHaveLength(1);
    expect(
      document.querySelector('[data-diagram-id="diagram-auth-cache"]')?.getAttribute(
        "data-focused",
      ),
    ).toBe("false");

    rerender(
      React.createElement(EvidenceRail, {
        section,
        focusedCitationId: "cite-cache",
      }),
    );

    const diagram = document.querySelector(
      '[data-diagram-id="diagram-auth-cache"]',
    );
    expect(screen.getAllByText("Auth and cache flow")).toHaveLength(1);
    expect(diagram?.getAttribute("data-focused")).toBe("true");
  });
});
