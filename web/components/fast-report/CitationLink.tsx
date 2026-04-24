"use client";

import type { FastReportCitation } from "@/lib/api";
import { cn } from "@/lib/utils";

export const FAST_REPORT_CITATION_FOCUS_EVENT =
  "autowiki:fast-report-citation-focus";

export type FastReportCitationFocusDetail = {
  sectionId: string;
  citationId: string;
};

export function dispatchFastReportCitationFocus(
  detail: FastReportCitationFocusDetail,
) {
  if (typeof window === "undefined") {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<FastReportCitationFocusDetail>(
      FAST_REPORT_CITATION_FOCUS_EVENT,
      { detail },
    ),
  );
}

function getCitationTitle(citation: FastReportCitation) {
  return `${citation.file_path}:${citation.start_line}-${citation.end_line}`;
}

export function CitationLink({
  citation,
  index,
  sectionId,
  isActive = false,
}: {
  citation: FastReportCitation;
  index: number;
  sectionId: string;
  isActive?: boolean;
}) {
  return (
    <button
      type="button"
      className={cn(
        "mx-0.5 inline-flex h-6 min-w-6 items-center justify-center rounded-full border px-2 align-middle text-[0.72rem] font-semibold leading-none transition-colors",
        isActive
          ? "border-slate-900 bg-slate-900 text-white"
          : "border-slate-300 bg-slate-100 text-slate-700 hover:border-slate-400 hover:bg-slate-200",
      )}
      title={`Jump to evidence in ${getCitationTitle(citation)}`}
      aria-label={`Jump to evidence for ${getCitationTitle(citation)}`}
      aria-controls={`evidence-${citation.id}`}
      data-citation-id={citation.id}
      onClick={() =>
        dispatchFastReportCitationFocus({
          sectionId,
          citationId: citation.id,
        })
      }
    >
      [{index + 1}]
    </button>
  );
}
