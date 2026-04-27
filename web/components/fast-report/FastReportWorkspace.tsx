"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  getFastReport,
  type FastReport,
  type FastReportAnalysisTrace,
  type FastReportSection,
} from "@/lib/api";
import { repoPath } from "@/lib/utils";
import {
  connectFastReportStream,
  type FastReportCompleteEvent,
} from "@/lib/ws";

import { ReportStack } from "./ReportStack";
import { EvidenceRail } from "./EvidenceRail";
import {
  FAST_REPORT_CITATION_FOCUS_EVENT,
  type FastReportCitationFocusDetail,
} from "./CitationLink";

export const FLOATING_ASSISTANT_HEIGHT_VAR = "--floating-assistant-height";
type WorkspaceStreamState = "idle" | "running" | "ready" | "error";

export type FastReportWorkspaceState = {
  report: FastReport | null;
  error: string | null;
  streamState: WorkspaceStreamState;
  isStarting: boolean;
  bufferedSections: FastReportSection[];
  bufferedCompletion: FastReportCompleteEvent | null;
  bufferedAnalysis: Record<string, FastReportAnalysisTrace>;
};

export function getWorkspaceBottomPadding() {
  return `calc(var(${FLOATING_ASSISTANT_HEIGHT_VAR}, 7rem) + 2rem)`;
}

export function createWorkspaceState(): FastReportWorkspaceState {
  return {
    report: null,
    error: null,
    streamState: "idle",
    isStarting: false,
    bufferedSections: [],
    bufferedCompletion: null,
    bufferedAnalysis: {},
  };
}

export function applyAnalysisEvent(
  state: FastReportWorkspaceState,
  event: { section_id: string; analysis_trace: FastReportAnalysisTrace },
): FastReportWorkspaceState {
  return {
    ...state,
    bufferedAnalysis: {
      ...state.bufferedAnalysis,
      [event.section_id]: event.analysis_trace,
    },
    streamState: "running",
  };
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001";

function mergeSection(section: FastReportSection, existing: FastReportSection[]) {
  const next = existing.filter((item) => item.id !== section.id);
  next.push(section);
  return next;
}

function getStreamStateForReport(report: Pick<FastReport, "status" | "error">) {
  if (report.status === "done") {
    return "ready" as const;
  }

  if (report.status === "failed") {
    return "error" as const;
  }

  return "running" as const;
}

export function applySectionEvent(
  state: FastReportWorkspaceState,
  section: FastReportSection,
): FastReportWorkspaceState {
  // Buffer the event when state.report is unset OR belongs to a different
  // report. Without the report_id guard, navigating from /fast/A → /fast/B
  // would merge B's streaming sections into A's stale state.report before
  // getFastReport(B) resolves.
  if (!state.report || state.report.id !== section.report_id) {
    return {
      ...state,
      streamState: "running",
      bufferedSections: mergeSection(section, state.bufferedSections),
    };
  }

  return {
    ...state,
    report: {
      ...state.report,
      active_section_id: section.id,
      sections: mergeSection(section, state.report.sections),
    },
    streamState: "running",
  };
}

export function applyReportCompleteEvent(
  state: FastReportWorkspaceState,
  event: FastReportCompleteEvent,
): FastReportWorkspaceState {
  if (!state.report || state.report.id !== event.report_id) {
    return {
      ...state,
      streamState: event.status === "failed" ? "error" : "ready",
      bufferedCompletion: event,
    };
  }

  return {
    ...state,
    report: {
      ...state.report,
      id: event.report_id,
      job_id: event.job_id,
      status: event.status,
      active_section_id: event.active_section_id,
    },
    streamState: event.status === "failed" ? "error" : "ready",
  };
}

export function applyLoadedReport(
  state: FastReportWorkspaceState,
  report: FastReport,
): FastReportWorkspaceState {
  const mergedSections = state.bufferedSections.reduce(
    (sections, section) => mergeSection(section, sections),
    report.sections,
  );
  const completedReport = state.bufferedCompletion
    ? {
        ...report,
        id: state.bufferedCompletion.report_id,
        job_id: state.bufferedCompletion.job_id,
        status: state.bufferedCompletion.status,
        active_section_id: state.bufferedCompletion.active_section_id,
      }
    : report;

  return {
    ...state,
    report: {
      ...completedReport,
      sections: mergedSections,
    },
    error: completedReport.status === "failed" ? completedReport.error : null,
    streamState: getStreamStateForReport(completedReport),
    isStarting: false,
    bufferedSections: [],
    bufferedCompletion: null,
  };
}

export function applyStreamError(
  state: FastReportWorkspaceState,
  message: string,
): FastReportWorkspaceState {
  return {
    ...state,
    error: message,
    streamState: "error",
    isStarting: false,
  };
}

export function getWorkspaceViewModel(
  state: FastReportWorkspaceState,
  activeReportId: string | null,
) {
  const activeSectionId =
    state.report?.active_section_id ?? state.report?.sections.at(-1)?.id ?? null;

  const hasTerminalError =
    state.streamState === "error" ||
    state.report?.status === "failed" ||
    Boolean(state.report?.status !== "failed" && state.error);

  return {
    activeSectionId,
    activeSection:
      state.report?.sections.find((section) => section.id === activeSectionId) ??
      null,
    error: state.report?.status === "failed" ? state.report.error : state.error,
    isLoading: Boolean(activeReportId && !state.report && !state.error),
    isRunning: hasTerminalError
      ? false
      : state.isStarting ||
          state.streamState === "running" ||
          state.report?.status === "queued",
  };
}

export function FastReportWorkspace({
  owner,
  repo,
  repoId,
  repoLabel,
  reportId,
  compatibilityMode = false,
}: {
  owner: string;
  repo: string;
  repoId: string;
  repoLabel: string;
  reportId?: string | null;
  compatibilityMode?: boolean;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [createdReportId, setCreatedReportId] = useState<string | null>(null);
  const [state, setState] = useState<FastReportWorkspaceState>(createWorkspaceState);
  const [evidenceSectionId, setEvidenceSectionId] = useState<string | null>(null);
  const [focusedCitationId, setFocusedCitationId] = useState<string | null>(null);
  const [streamNonce, setStreamNonce] = useState(0);
  const consumedInitialQuestionRef = useRef<string | null>(null);
  const activeReportId = reportId ?? createdReportId;
  const previousReportId = useRef<string | null>(activeReportId);

  // Reset workspace state when navigating between reports. Without this,
  // bufferedSections from a failed load (e.g. /fast/A → /fast/B where
  // getFastReport(B) errors) would later be merged into a different report.
  useEffect(() => {
    if (previousReportId.current === activeReportId) {
      return;
    }
    previousReportId.current = activeReportId;
    setState(createWorkspaceState());
    setEvidenceSectionId(null);
    setFocusedCitationId(null);
  }, [activeReportId]);

  useEffect(() => {
    if (!activeReportId) {
      return;
    }

    const reportIdToLoad = activeReportId;
    let cancelled = false;

    async function loadReport() {
      try {
        const nextReport = await getFastReport(repoId, reportIdToLoad);
        if (cancelled) {
          return;
        }
        setState((current) => applyLoadedReport(current, nextReport));
      } catch (err) {
        if (cancelled) {
          return;
        }
        setState((current) => ({
          ...current,
          error: err instanceof Error ? err.message : String(err),
          streamState: "error",
          isStarting: false,
        }));
      } finally {
        if (!cancelled) {
          setState((current) => ({ ...current, isStarting: false }));
        }
      }
    }

    void loadReport();

    return () => {
      cancelled = true;
    };
  }, [activeReportId, repoId]);

  const beginReport = useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed) {
        return;
      }

      setState((current) => ({
        ...current,
        isStarting: true,
        streamState: "running",
        error: null,
        report: current.report
          ? {
              ...current.report,
              status: "queued",
            }
          : current.report,
      }));

      try {
        const res = await fetch(`${API_URL}/api/repos/${repoId}/fast-reports`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question: trimmed,
            report_id: activeReportId,
          }),
        });
        if (!res.ok) {
          throw new Error(await res.text());
        }
        const next = (await res.json()) as {
          report_id: string;
        };
        const destination = `${repoPath(owner, repo, repoId)}/fast/${encodeURIComponent(next.report_id)}`;
        setCreatedReportId(next.report_id);
        setStreamNonce((current) => current + 1);
        router.replace(destination);
      } catch (err) {
        setState((current) => ({
          ...current,
          isStarting: false,
          error: err instanceof Error ? err.message : String(err),
          streamState: "error",
        }));
      }
    },
    [activeReportId, owner, repo, repoId, router],
  );

  useEffect(() => {
    const initialQuestion = searchParams.get("q");
    // Reset the dedup ref once the URL no longer carries ?q so that a future
    // re-navigation with ?q= can fire again.
    if (!initialQuestion) {
      consumedInitialQuestionRef.current = null;
      return;
    }
    // Track consumed question by content alone, not by activeReportId. The
    // activeReportId-keyed approach fires a second POST during the null → realId
    // transition that happens when setCreatedReportId + router.replace are not
    // batched into a single render with the useSearchParams update.
    if (consumedInitialQuestionRef.current === initialQuestion) {
      return;
    }
    consumedInitialQuestionRef.current = initialQuestion;
    setTimeout(() => {
      void beginReport(initialQuestion);
    }, 0);
  }, [beginReport, searchParams]);

  const handleSectionComplete = useCallback((section: FastReportSection) => {
    setState((current) => applySectionEvent(current, section));
  }, []);

  const handleReportComplete = useCallback(
    (event: FastReportCompleteEvent) => {
      setState((current) => applyReportCompleteEvent(current, event));
    },
    [],
  );

  const handleStreamError = useCallback((message: string) => {
    setState((current) => applyStreamError(current, message));
  }, []);

  const handleAnalysisUpdate = useCallback(
    (sectionId: string, trace: FastReportAnalysisTrace) => {
      setState((current) =>
        applyAnalysisEvent(current, { section_id: sectionId, analysis_trace: trace }),
      );
    },
    [],
  );

  useEffect(() => {
    if (!activeReportId) {
      return;
    }

    const ws = connectFastReportStream(repoId, activeReportId, {
      onSectionComplete: handleSectionComplete,
      onReportComplete: handleReportComplete,
      onAnalysisUpdate: handleAnalysisUpdate,
      onError: handleStreamError,
    });
    return () => {
      ws.close();
    };
  }, [
    activeReportId,
    handleAnalysisUpdate,
    handleReportComplete,
    handleSectionComplete,
    handleStreamError,
    repoId,
    streamNonce,
  ]);

  const bottomPadding = useMemo(() => getWorkspaceBottomPadding(), []);
  const view = getWorkspaceViewModel(state, activeReportId);
  const railSection =
    state.report?.sections.find((section) => section.id === evidenceSectionId) ??
    view.activeSection;
  const railFocusedCitationId =
    railSection?.citations.some((citation) => citation.id === focusedCitationId)
      ? focusedCitationId
      : null;

  useEffect(() => {
    function handleFocus(event: Event) {
      const { detail } = event as CustomEvent<FastReportCitationFocusDetail>;
      const matchingSection = state.report?.sections.find(
        (section) => section.id === detail.sectionId,
      );

      if (!matchingSection) {
        return;
      }

      setEvidenceSectionId(matchingSection.id);
      setFocusedCitationId(detail.citationId);
    }

    window.addEventListener(
      FAST_REPORT_CITATION_FOCUS_EVENT,
      handleFocus as EventListener,
    );
    return () => {
      window.removeEventListener(
        FAST_REPORT_CITATION_FOCUS_EVENT,
        handleFocus as EventListener,
      );
    };
  }, [state.report?.sections]);

  return (
    <div className="mx-auto flex w-full max-w-[1500px] flex-1 flex-col px-4 pb-8 pt-6 sm:px-6 lg:px-10">
      <div className="mb-8 flex flex-col gap-3 border-b border-slate-200 pb-5">
        <div className="flex flex-wrap items-center gap-3 text-[0.72rem] font-semibold uppercase tracking-[0.26em] text-slate-500">
          <span>Fast report</span>
          {compatibilityMode ? <span>Compatibility route</span> : null}
          {state.report?.status ? <span>Status: {state.report.status}</span> : null}
        </div>
        <div className="flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">
              Code-grounded answers for {repoLabel}
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600 sm:text-[0.96rem]">
              Follow the assistant prompts below to build a persistent fast report
              instead of the old chat transcript.
            </p>
          </div>
          {activeReportId ? (
            <div className="text-sm leading-6 text-slate-500">
              Report ID:{" "}
              <span className="font-mono text-[0.78rem] text-slate-700">
                {activeReportId}
              </span>
            </div>
          ) : null}
        </div>
      </div>

      {view.error ? (
        <div className="mb-6 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-700">
          {view.error}
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-10 lg:grid-cols-[minmax(0,1.7fr)_minmax(280px,0.95fr)] lg:gap-12">
        <section
          className="min-h-0"
          style={{ paddingBottom: bottomPadding }}
        >
          <ReportStack
            sections={state.report?.sections ?? []}
            activeSectionId={view.activeSectionId}
            isRunning={view.isRunning}
          />
          {view.isLoading ? (
            <div className="mt-6 max-w-3xl text-sm leading-6 text-slate-500">
              Loading report workspace...
            </div>
          ) : null}
        </section>

        <aside
          className="min-h-0 border-t border-slate-200 pt-6 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0"
          style={{ paddingBottom: bottomPadding }}
        >
          <EvidenceRail
            section={railSection}
            focusedCitationId={railFocusedCitationId}
            analysisTrace={
              railSection
                ? state.bufferedAnalysis[railSection.id]
                : state.bufferedAnalysis[view.activeSectionId ?? ""]
            }
          />
        </aside>
      </div>
    </div>
  );
}
