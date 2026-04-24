"use client";

import type { FastReportDiagram } from "@/lib/api";
import { cn } from "@/lib/utils";
import { MermaidBlock } from "@/components/WikiPage";

const MERMAID_SOURCE_RE =
  /^(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|mindmap|timeline|gitGraph|requirementDiagram|quadrantChart|sankey-beta|architecture|block-beta)\b/i;

export function isMermaidDiagram(diagram: FastReportDiagram) {
  return (
    diagram.type.toLowerCase() === "mermaid" ||
    MERMAID_SOURCE_RE.test(diagram.source.trim())
  );
}

export function DiagramPanel({
  diagrams,
  focusedCitationId,
}: {
  diagrams: FastReportDiagram[];
  focusedCitationId?: string | null;
}) {
  if (diagrams.length === 0) {
    return null;
  }

  return (
    <div className="space-y-4">
      {diagrams.map((diagram) => {
        const isFocused = Boolean(
          focusedCitationId && diagram.citations.includes(focusedCitationId),
        );

        return (
          <section
            key={diagram.id}
            data-diagram-id={diagram.id}
            data-focused={isFocused ? "true" : "false"}
            className={cn(
              "rounded-[1.4rem] border bg-white p-4 shadow-sm transition-colors",
              isFocused ? "border-slate-900 bg-slate-50" : "border-slate-200",
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-semibold text-slate-900">{diagram.title}</p>
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-slate-500">
                {diagram.type}
              </span>
            </div>
            {diagram.caption ? (
              <p className="mt-2 text-sm leading-6 text-slate-600">
                {diagram.caption}
              </p>
            ) : null}
            {diagram.reason ? (
              <p className="mt-1 text-xs leading-5 text-slate-500">
                {diagram.reason}
              </p>
            ) : null}

            <div className="mt-4">
              {isMermaidDiagram(diagram) ? (
                <MermaidBlock>{diagram.source}</MermaidBlock>
              ) : (
                <pre className="overflow-x-auto rounded-2xl border border-slate-200 bg-slate-950 p-4 font-mono text-[0.78rem] leading-6 text-slate-100">
                  <code>{diagram.source}</code>
                </pre>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
