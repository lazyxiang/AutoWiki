"use client";

import { useMemo, useState } from "react";

import type { FastReportCitation, FastReportEvidenceBlock } from "@/lib/api";
import { cn } from "@/lib/utils";

export type VisibleEvidenceLine = {
  lineNumber: number;
  content: string;
};

export const EVIDENCE_EXPANSION_STEP = 15;

export function getInitialEvidenceContext(block: FastReportEvidenceBlock) {
  if (block.is_collapsed) {
    return block.default_context;
  }

  return Math.max(block.default_context, block.expanded_context);
}

export function getVisibleEvidenceRange(
  block: FastReportEvidenceBlock,
  context: number,
) {
  return {
    start: Math.max(block.full_start, block.snippet_start - context),
    end: Math.min(block.full_end, block.snippet_end + context),
  };
}

export function getVisibleEvidenceLines(
  block: FastReportEvidenceBlock,
  context: number,
): VisibleEvidenceLine[] {
  const { start, end } = getVisibleEvidenceRange(block, context);
  const lines = block.code.split("\n");

  const visible: VisibleEvidenceLine[] = [];
  for (let lineNumber = start; lineNumber <= end; lineNumber += 1) {
    const index = lineNumber - block.full_start;
    visible.push({
      lineNumber,
      content: lines[index] ?? "",
    });
  }

  return visible;
}

function formatLineRange(citation: FastReportCitation) {
  return `${citation.start_line}-${citation.end_line}`;
}

export function EvidenceBlock({
  citation,
  block,
  isFocused = false,
}: {
  citation: FastReportCitation;
  block: FastReportEvidenceBlock;
  isFocused?: boolean;
}) {
  const [context, setContext] = useState(() => getInitialEvidenceContext(block));
  const visibleLines = useMemo(
    () => getVisibleEvidenceLines(block, context),
    [block, context],
  );
  const visibleRange = useMemo(
    () => getVisibleEvidenceRange(block, context),
    [block, context],
  );
  const isCollapsed = context <= block.default_context;
  const canExpand =
    visibleRange.start > block.full_start || visibleRange.end < block.full_end;

  return (
    <section
      data-evidence-citation-id={citation.id}
      data-focused={isFocused ? "true" : "false"}
      className={cn(
        "rounded-[1.4rem] border bg-white p-4 shadow-sm transition-colors",
        isFocused ? "border-slate-900 bg-slate-50" : "border-slate-200",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">
            {citation.file_path}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span>Lines {formatLineRange(citation)}</span>
            {block.symbol_path ? <span>{block.symbol_path}</span> : null}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {canExpand ? (
            <button
              type="button"
              className="rounded-full border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-100"
              onClick={() => setContext((current) => current + EVIDENCE_EXPANSION_STEP)}
            >
              Expand +{EVIDENCE_EXPANSION_STEP}
            </button>
          ) : null}
          {!isCollapsed ? (
            <button
              type="button"
              className="rounded-full border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-100"
              onClick={() => setContext(block.default_context)}
            >
              Collapse
            </button>
          ) : null}
        </div>
      </div>

      <div className="mt-4 overflow-hidden rounded-2xl border border-slate-200 bg-slate-950">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-[0.78rem] leading-6 text-slate-100">
            <tbody>
              {visibleLines.map((line) => {
                const isSnippetLine =
                  line.lineNumber >= block.snippet_start &&
                  line.lineNumber <= block.snippet_end;

                return (
                  <tr
                    key={line.lineNumber}
                    className={cn(
                      isSnippetLine ? "bg-slate-900" : "bg-slate-950",
                      isFocused && isSnippetLine ? "ring-1 ring-inset ring-amber-300" : "",
                    )}
                  >
                    <td className="w-12 select-none border-r border-slate-800 px-3 text-right text-slate-500">
                      {line.lineNumber}
                    </td>
                    <td className="px-4 py-0.5 whitespace-pre">
                      {line.content || " "}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
