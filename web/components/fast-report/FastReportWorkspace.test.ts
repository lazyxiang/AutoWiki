import { describe, expect, it } from "vitest";

import type { FastReport, FastReportSection } from "@/lib/api";
import type { FastReportCompleteEvent } from "@/lib/ws";

import {
  FLOATING_ASSISTANT_HEIGHT_VAR,
  applyLoadedReport,
  applyReportCompleteEvent,
  applySectionEvent,
  createWorkspaceState,
  getWorkspaceBottomPadding,
  getWorkspaceViewModel,
} from "./FastReportWorkspace";
import { sortSectionsForDisplay } from "./ReportStack";

function makeSection(
  overrides: Partial<FastReportSection> = {},
): FastReportSection {
  return {
    id: "section-1",
    report_id: "report-1",
    query: "Where is auth wired?",
    title: "Authentication flow",
    summary: "Tracks the login boundary.",
    markdown: "Section body",
    citations: [],
    evidence_blocks: [],
    related_wiki_pages: [],
    related_diagrams: [],
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
    status: "queued",
    error: null,
    active_section_id: null,
    created_at: "2026-04-24T00:00:00Z",
    expires_at: "2026-05-01T00:00:00Z",
    sections: [],
    ...overrides,
  };
}

function makeCompleteEvent(
  overrides: Partial<FastReportCompleteEvent> = {},
): FastReportCompleteEvent {
  return {
    report_id: "report-1",
    job_id: "job-1",
    active_section_id: "section-1",
    status: "done",
    ...overrides,
  };
}

describe("FastReportWorkspace", () => {
  it("reserves bottom space from the floating assistant height variable", () => {
    expect(FLOATING_ASSISTANT_HEIGHT_VAR).toBe("--floating-assistant-height");
    expect(getWorkspaceBottomPadding()).toBe(
      "calc(var(--floating-assistant-height, 7rem) + 2rem)",
    );
  });

  it("treats persisted done reports as ready on first load", () => {
    const state = applyLoadedReport(
      createWorkspaceState(),
      makeReport({
        status: "done",
        active_section_id: "section-1",
        sections: [makeSection()],
      }),
    );

    expect(getWorkspaceViewModel(state, "report-1")).toMatchObject({
      error: null,
      isLoading: false,
      isRunning: false,
      activeSectionId: "section-1",
    });
    expect(state.report?.status).toBe("done");
  });

  it("treats persisted failed reports as terminal and surfaces the API error", () => {
    const state = applyLoadedReport(
      createWorkspaceState(),
      makeReport({
        status: "failed",
        error: "Planner exhausted retries",
      }),
    );

    expect(getWorkspaceViewModel(state, "report-1")).toMatchObject({
      error: "Planner exhausted retries",
      isLoading: false,
      isRunning: false,
    });
    expect(state.streamState).toBe("error");
  });

  it("buffers early stream events and merges them after the initial fetch resolves", () => {
    const earlySection = makeSection({
      id: "section-early",
      title: "Early section",
      created_at: "2026-04-24T00:30:00Z",
    });

    const stateWithBufferedEvents = applyReportCompleteEvent(
      applySectionEvent(createWorkspaceState(), earlySection),
      makeCompleteEvent({
        active_section_id: "section-early",
      }),
    );

    const loaded = applyLoadedReport(
      stateWithBufferedEvents,
      makeReport({
        status: "queued",
        sections: [],
      }),
    );

    expect(loaded.report).not.toBeNull();
    expect(loaded.report?.status).toBe("done");
    expect(loaded.report?.active_section_id).toBe("section-early");
    expect(loaded.report?.sections.map((section) => section.id)).toEqual([
      "section-early",
    ]);
    expect(getWorkspaceViewModel(loaded, "report-1")).toMatchObject({
      error: null,
      isLoading: false,
      isRunning: false,
      activeSectionId: "section-early",
    });
  });
});

describe("sortSectionsForDisplay", () => {
  it("shows report sections in creation order", () => {
    expect(
      sortSectionsForDisplay([
        { id: "b", created_at: "2026-04-23T12:00:00Z" },
        { id: "a", created_at: "2026-04-23T09:00:00Z" },
        { id: "c", created_at: "2026-04-23T15:00:00Z" },
      ]),
    ).toEqual(["a", "b", "c"]);
  });
});
