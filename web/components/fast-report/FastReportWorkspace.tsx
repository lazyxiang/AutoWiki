"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { FileSearch, Sparkles } from "lucide-react";

import {
  getFastReport,
  startFastReport,
  type FastReport,
  type FastReportSection,
} from "@/lib/api";
import { repoPath } from "@/lib/utils";
import { useFastReportStream, type FastReportCompleteEvent } from "@/lib/ws";

import { ReportStack } from "./ReportStack";

export const FLOATING_ASSISTANT_HEIGHT_VAR = "--floating-assistant-height";

export function getWorkspaceBottomPadding() {
  return `calc(var(${FLOATING_ASSISTANT_HEIGHT_VAR}, 7rem) + 2rem)`;
}

function mergeSection(section: FastReportSection, existing: FastReportSection[]) {
  const next = existing.filter((item) => item.id !== section.id);
  next.push(section);
  return next;
}

function updateReportFromEvent(
  report: FastReport | null,
  section: FastReportSection,
) {
  if (!report) {
    return null;
  }

  return {
    ...report,
    active_section_id: section.id,
    sections: mergeSection(section, report.sections),
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
  const [report, setReport] = useState<FastReport | null>(null);
  const [createdReportId, setCreatedReportId] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<"idle" | "running" | "ready" | "error">("idle");
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestedInitialReport = useRef(false);
  const activeReportId = reportId ?? createdReportId;

  useEffect(() => {
    if (!activeReportId) {
      return;
    }

    let cancelled = false;

    async function loadReport() {
      try {
        const nextReport = await getFastReport(repoId, activeReportId);
        if (cancelled) {
          return;
        }
        setReport(nextReport);
        setError(null);
        setStreamState(nextReport.status === "complete" ? "ready" : "running");
      } catch (err) {
        if (cancelled) {
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
        setStreamState("error");
      } finally {
        if (!cancelled) {
          setIsStarting(false);
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

      setIsStarting(true);
      setStreamState("running");
      setError(null);

      try {
        const next = await startFastReport(repoId, trimmed);
        setCreatedReportId(next.report_id);
        router.replace(
          `${repoPath(owner, repo, repoId)}/fast/${encodeURIComponent(next.report_id)}`,
        );
      } catch (err) {
        setIsStarting(false);
        setError(err instanceof Error ? err.message : String(err));
        setStreamState("error");
      }
    },
    [owner, repo, repoId, router],
  );

  useEffect(() => {
    if (requestedInitialReport.current || activeReportId) {
      return;
    }

    const initialQuestion = searchParams.get("q");
    if (!initialQuestion) {
      return;
    }

    requestedInitialReport.current = true;
    setTimeout(() => void beginReport(initialQuestion), 0);
  }, [activeReportId, beginReport, searchParams]);

  const handleSectionComplete = useCallback((section: FastReportSection) => {
    setReport((current) => updateReportFromEvent(current, section));
    setStreamState("running");
  }, []);

  const handleReportComplete = useCallback(
    (event: FastReportCompleteEvent) => {
      setReport((current) =>
        current
          ? {
              ...current,
              id: event.report_id,
              status: "complete",
              active_section_id: current.active_section_id,
            }
          : current,
      );
      setStreamState("ready");
    },
    [],
  );

  const handleStreamError = useCallback((message: string) => {
    setError(message);
    setStreamState("error");
  }, []);

  useFastReportStream(
    repoId,
    activeReportId,
    handleSectionComplete,
    handleReportComplete,
    handleStreamError,
  );

  const activeSectionId = report?.active_section_id ?? report?.sections.at(-1)?.id ?? null;
  const activeSection = report?.sections.find((section) => section.id === activeSectionId) ?? null;
  const bottomPadding = useMemo(() => getWorkspaceBottomPadding(), []);
  const isLoading = Boolean(activeReportId && !report && !error);
  const isRunning =
    isStarting || streamState === "running" || report?.status === "running";

  return (
    <div className="mx-auto flex w-full max-w-[1500px] flex-1 flex-col px-4 pb-8 pt-6 sm:px-6 lg:px-10">
      <div className="mb-8 flex flex-col gap-3 border-b border-slate-200 pb-5">
        <div className="flex flex-wrap items-center gap-3 text-[0.72rem] font-semibold uppercase tracking-[0.26em] text-slate-500">
          <span>Fast report</span>
          {compatibilityMode ? <span>Compatibility route</span> : null}
          {report?.status ? <span>Status: {report.status}</span> : null}
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

      {error ? (
        <div className="mb-6 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-700">
          {error}
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-10 lg:grid-cols-[minmax(0,1.7fr)_minmax(280px,0.95fr)] lg:gap-12">
        <section
          className="min-h-0"
          style={{ paddingBottom: bottomPadding }}
        >
          <ReportStack
            sections={report?.sections ?? []}
            activeSectionId={activeSectionId}
            isRunning={isRunning}
          />
          {isLoading ? (
            <div className="mt-6 max-w-3xl text-sm leading-6 text-slate-500">
              Loading report workspace...
            </div>
          ) : null}
        </section>

        <aside
          className="min-h-0 border-t border-slate-200 pt-6 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0"
          style={{ paddingBottom: bottomPadding }}
        >
          <div className="sticky top-6 space-y-6">
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-[0.72rem] font-semibold uppercase tracking-[0.24em] text-slate-500">
                <FileSearch className="h-4 w-4" />
                Evidence rail
              </div>
              <p className="text-sm leading-6 text-slate-600">
                The citation rail lands in the next task. This shell keeps the
                desktop workspace shape and reserves the column without wiring
                citation interactions yet.
              </p>
            </div>

            <div className="rounded-[1.75rem] bg-slate-100/80 p-5">
              <div className="flex items-center gap-2 text-sm font-medium text-slate-900">
                <Sparkles className="h-4 w-4 text-slate-500" />
                Active section
              </div>
              <div className="mt-3 text-sm leading-6 text-slate-600">
                {activeSection ? (
                  <>
                    <p className="font-medium text-slate-900">{activeSection.title}</p>
                    <p className="mt-2">
                      {activeSection.summary ??
                        "Supporting code evidence will appear here once the rail is implemented."}
                    </p>
                  </>
                ) : (
                  <p>
                    Ask a question from the floating assistant to populate the
                    report stack and related evidence context.
                  </p>
                )}
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
