"use client";

import { useEffect, useMemo, useRef } from "react";
import { FileSearch } from "lucide-react";

import type { FastReportAnalysisTrace, FastReportSection } from "@/lib/api";
import { AnalysisTracePanel } from "./AnalysisTracePanel";
import { DiagramPanel } from "./DiagramPanel";
import { EvidenceBlock } from "./EvidenceBlock";

export type EvidenceRailItem = {
  citation: FastReportSection["citations"][number];
  blocks: FastReportSection["evidence_blocks"];
  diagrams: FastReportSection["related_diagrams"];
  targetCitationIds: string[];
};

export function buildEvidenceRailItems(
  section: FastReportSection | null,
): EvidenceRailItem[] {
  if (!section) {
    return [];
  }

  const renderedDiagramIds = new Set<string>();
  const diagramTargetById = new Map<string, string>();
  const items: EvidenceRailItem[] = [];

  for (const citation of section.citations) {
    const blocks = section.evidence_blocks.filter(
      (block) => block.citation_id === citation.id,
    );
    const relatedDiagrams = section.related_diagrams.filter((diagram) =>
      diagram.citations.includes(citation.id),
    );
    const diagrams = relatedDiagrams.filter((diagram) => {
      if (renderedDiagramIds.has(diagram.id)) {
        return false;
      }
      renderedDiagramIds.add(diagram.id);
      diagramTargetById.set(diagram.id, citation.id);
      return true;
    });

    if (blocks.length === 0 && diagrams.length === 0 && relatedDiagrams.length === 0) {
      continue;
    }

    if (blocks.length === 0 && diagrams.length === 0) {
      const aliasedTargetCitationId = relatedDiagrams
        .map((diagram) => diagramTargetById.get(diagram.id) ?? null)
        .find((targetCitationId) => targetCitationId !== null);

      if (aliasedTargetCitationId) {
        const targetItem = items.find(
          (item) => item.citation.id === aliasedTargetCitationId,
        );
        if (targetItem && !targetItem.targetCitationIds.includes(citation.id)) {
          targetItem.targetCitationIds.push(citation.id);
        }
      }
      continue;
    }

    items.push({
      citation,
      blocks,
      diagrams,
      targetCitationIds: [citation.id],
    });
  }

  return items;
}

export function EvidenceRail({
  section,
  focusedCitationId = null,
  analysisTrace,
}: {
  section: FastReportSection | null;
  focusedCitationId?: string | null;
  analysisTrace?: FastReportAnalysisTrace;
}) {
  const items = useMemo(() => buildEvidenceRailItems(section), [section]);
  const itemRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const activeFocusedCitationId = items.some((item) =>
    item.targetCitationIds.includes(focusedCitationId ?? ""),
  )
    ? focusedCitationId
    : null;

  useEffect(() => {
    if (!activeFocusedCitationId) {
      return;
    }

    itemRefs.current[activeFocusedCitationId]?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }, [activeFocusedCitationId]);

  if (!section) {
    return (
      <div className="sticky top-6 space-y-3">
        <div className="flex items-center gap-2 text-[0.72rem] font-semibold uppercase tracking-[0.24em] text-slate-500">
          <FileSearch className="h-4 w-4" />
          Evidence rail
        </div>
        {analysisTrace?.files?.length ? (
          <AnalysisTracePanel trace={analysisTrace} />
        ) : (
          <p className="text-sm leading-6 text-slate-600">
            Ask a question from the floating assistant to populate evidence blocks
            and related diagrams for the active section.
          </p>
        )}
      </div>
    );
  }

  return (
    <div
      className="sticky top-6 space-y-6"
      data-evidence-rail-section-id={section.id}
    >
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-[0.72rem] font-semibold uppercase tracking-[0.24em] text-slate-500">
          <FileSearch className="h-4 w-4" />
          Evidence rail
        </div>
        <div className="rounded-[1.75rem] bg-slate-100/80 p-5">
          <p className="text-sm font-medium text-slate-900">{section.title}</p>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            {section.summary ??
              "Follow inline citations in the report body to focus supporting code evidence."}
          </p>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="rounded-[1.4rem] border border-dashed border-slate-200 bg-slate-50/80 px-4 py-5 text-sm leading-6 text-slate-500">
          This section does not have code evidence blocks yet.
        </div>
      ) : (
        <div className="space-y-5">
          {items.map((item) => {
            const isFocused = item.targetCitationIds.includes(
              activeFocusedCitationId ?? "",
            );

            return (
              <div
                key={item.citation.id}
                id={`evidence-${item.citation.id}`}
                data-evidence-target={item.citation.id}
                data-evidence-targets={item.targetCitationIds.join(",")}
                data-focused={isFocused ? "true" : "false"}
                ref={(node) => {
                  item.targetCitationIds.forEach((citationId) => {
                    itemRefs.current[citationId] = node;
                  });
                }}
                className="scroll-mt-24 space-y-3"
              >
                {item.targetCitationIds
                  .filter((citationId) => citationId !== item.citation.id)
                  .map((citationId) => (
                    <span
                      key={citationId}
                      id={`evidence-${citationId}`}
                      data-evidence-alias-for={item.citation.id}
                      className="sr-only"
                      aria-hidden="true"
                    >
                      Evidence alias target for {citationId}
                    </span>
                  ))}
                {item.blocks.map((block, index) => (
                  <EvidenceBlock
                    key={`${item.citation.id}-${block.full_start}-${index}`}
                    citation={item.citation}
                    block={block}
                    isFocused={isFocused}
                  />
                ))}
                <DiagramPanel
                  diagrams={item.diagrams}
                  focusedCitationId={activeFocusedCitationId}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
