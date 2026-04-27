// @vitest-environment jsdom

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, beforeEach, vi } from "vitest";

import type { FastReport, FastReportSection } from "@/lib/api";
import type { FastReportCompleteEvent } from "@/lib/ws";

const {
  getFastReportMock,
  replaceMock,
  connectFastReportStreamMock,
  searchParamsState,
} = vi.hoisted(() => ({
  getFastReportMock: vi.fn(),
  replaceMock: vi.fn(),
  connectFastReportStreamMock: vi.fn(),
  searchParamsState: { value: new URLSearchParams() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: () => searchParamsState.value,
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getFastReport: getFastReportMock,
  };
});

vi.mock("@/lib/ws", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ws")>("@/lib/ws");
  return {
    ...actual,
    connectFastReportStream: connectFastReportStreamMock,
  };
});

import {
  FLOATING_ASSISTANT_HEIGHT_VAR,
  FastReportWorkspace,
  applyLoadedReport,
  applyReportCompleteEvent,
  applySectionEvent,
  applyStreamError,
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
  beforeEach(() => {
    getFastReportMock.mockReset();
    replaceMock.mockReset();
    connectFastReportStreamMock.mockReset();
    vi.unstubAllGlobals();
    searchParamsState.value = new URLSearchParams();
    connectFastReportStreamMock.mockImplementation(() => ({ close: vi.fn() }));
  });

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

  it("preserves prior sections and marks the report running while an append is queued", () => {
    const baseState = applyLoadedReport(
      createWorkspaceState(),
      makeReport({
        status: "done",
        active_section_id: "section-1",
        sections: [makeSection()],
      }),
    );

    const appending = {
      ...baseState,
      isStarting: true,
      streamState: "running" as const,
      error: null,
      report: baseState.report
        ? {
            ...baseState.report,
            status: "queued",
          }
        : null,
    };

    expect(appending.report?.sections.map((section) => section.id)).toEqual([
      "section-1",
    ]);
    expect(getWorkspaceViewModel(appending, "report-1")).toMatchObject({
      isRunning: true,
      activeSectionId: "section-1",
    });
  });

  it("stops reporting running when a stream error lands after a queued snapshot", () => {
    const errored = applyStreamError(
      applyLoadedReport(
        createWorkspaceState(),
        makeReport({
          status: "queued",
        }),
      ),
      "WebSocket error",
    );

    expect(getWorkspaceViewModel(errored, "report-1")).toMatchObject({
      error: "WebSocket error",
      isLoading: false,
      isRunning: false,
    });
    expect(errored.report?.status).toBe("queued");
    expect(errored.streamState).toBe("error");
  });

  it("hydrates an existing report, appends a follow-up into the same report, and streams the new section", async () => {
    const persistedSection = makeSection({
      id: "section-1",
      title: "Persisted section",
      markdown: "Persisted body",
      query: "Original question",
    });
    const appendedSection = makeSection({
      id: "section-2",
      title: "Follow-up section",
      markdown: "Follow-up body",
      query: "Follow-up question",
      created_at: "2026-04-24T02:00:00Z",
    });
    let sectionHandler: ((section: FastReportSection) => void) | null = null;
    let completeHandler: ((event: FastReportCompleteEvent) => void) | null = null;

    searchParamsState.value = new URLSearchParams("q=Follow-up question");
    getFastReportMock.mockResolvedValue(
      makeReport({
        id: "report-1",
        status: "done",
        active_section_id: "section-1",
        sections: [persistedSection],
      }),
    );
    connectFastReportStreamMock.mockImplementation(
      (
        _repoId: string,
        _reportId: string,
        handlers: {
          onSectionComplete: (section: FastReportSection) => void;
          onReportComplete: (event: FastReportCompleteEvent) => void;
          onError: (msg: string) => void;
        },
      ) => {
        sectionHandler = handlers.onSectionComplete;
        completeHandler = handlers.onReportComplete;
        return { close: vi.fn() };
      },
    );

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        report_id: "report-1",
        job_id: "report-1",
        section_id: "section-2",
        status: "queued",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      React.createElement(FastReportWorkspace, {
        owner: "openai",
        repo: "autowiki",
        repoId: "repo-1",
        repoLabel: "openai/autowiki",
        reportId: "report-1",
      }),
    );

    await screen.findByRole("heading", { name: "Persisted section" });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "http://localhost:3001/api/repos/repo-1/fast-reports",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question: "Follow-up question",
            report_id: "report-1",
          }),
        },
      );
    });

    expect(replaceMock).toHaveBeenCalledWith("/repo-1/autowiki/fast/report-1");
    expect(connectFastReportStreamMock).toHaveBeenCalledWith(
      "repo-1",
      "report-1",
      expect.objectContaining({
        onSectionComplete: expect.any(Function),
        onReportComplete: expect.any(Function),
        onError: expect.any(Function),
      }),
    );

    sectionHandler?.(appendedSection);
    completeHandler?.(
      makeCompleteEvent({
        report_id: "report-1",
        active_section_id: "section-2",
      }),
    );

    await screen.findByRole("heading", { name: "Follow-up section" });
    expect(
      screen.getByRole("heading", { name: "Persisted section" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "Follow-up section" }),
    ).toBeTruthy();
  });

  it("fires exactly one POST during the null→realId transition when ?q= drives report creation", async () => {
    // Verifies that when beginReport() calls setCreatedReportId() + router.replace()
    // before useSearchParams() has cleared ?q=, the dedup ref prevents a second POST
    // from being issued on the intermediate re-render where activeReportId is the new
    // id but searchParams still contains ?q=.
    searchParamsState.value = new URLSearchParams("q=Create question");
    getFastReportMock.mockResolvedValue(
      makeReport({ id: "new-report", status: "queued", sections: [] }),
    );

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        report_id: "new-report",
        job_id: "new-report",
        section_id: "sec-1",
        status: "queued",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      React.createElement(FastReportWorkspace, {
        owner: "openai",
        repo: "autowiki",
        repoId: "repo-1",
        repoLabel: "openai/autowiki",
        // No reportId — simulates /fast/new navigation
      }),
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    // Simulate the intermediate re-render where searchParams still has ?q= but
    // activeReportId has already transitioned from null to the newly created id.
    // The dedup ref must suppress any additional POST.
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:3001/api/repos/repo-1/fast-reports",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining("Create question"),
      }),
    );
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
