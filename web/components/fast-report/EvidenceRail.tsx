"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { FileSearch } from "lucide-react";

import type { FastReportSection } from "@/lib/api";

import {
  FAST_REPORT_CITATION_FOCUS_EVENT,
  type FastReportCitationFocusDetail,
} from "./CitationLink";
import { DiagramPanel } from "./DiagramPanel";
import { EvidenceBlock } from "./EvidenceBlock";

export type EvidenceRailItem = {
  citation: FastReportSection["citations"][number];
  blocks: FastReportSection["evidence_blocks"];
  diagrams: FastReportSection["related_diagrams"];
};

export function buildEvidenceRailItems(
  section: FastReportSection | null,
): EvidenceRailItem[] {
  if (!section) {
    return [];
  }

  return section.citations.flatMap((citation) => {
    const blocks = section.evidence_blocks.filter(
      (block) => block.citation_id === citation.id,
    );
    const diagrams = section.related_diagrams.filter((diagram) =>
      diagram.citations.includes(citation.id),
    );

    if (blocks.length === 0 && diagrams.length === 0) {
      return [];
    }

    return [{ citation, blocks, diagrams }];
  });
}

export function EvidenceRail({
  section,
}: {
  section: FastReportSection | null;
}) {
  const items = useMemo(() => buildEvidenceRailItems(section), [section]);
  const [focusedCitationId, setFocusedCitationId] = useState<string | null>(null);
  const itemRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const activeFocusedCitationId = items.some(
    (item) => item.citation.id === focusedCitationId,
  )
    ? focusedCitationId
    : null;

  useEffect(() => {
    function handleFocus(event: Event) {
      const { detail } = event as CustomEvent<FastReportCitationFocusDetail>;
      if (!section || detail.sectionId !== section.id) {
        return;
      }
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
  }, [section]);

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
        <p className="text-sm leading-6 text-slate-600">
          Ask a question from the floating assistant to populate evidence blocks
          and related diagrams for the active section.
        </p>
      </div>
    );
  }

  return (
    <div className="sticky top-6 space-y-6">
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
            const isFocused = item.citation.id === activeFocusedCitationId;

            return (
              <div
                key={item.citation.id}
                id={`evidence-${item.citation.id}`}
                ref={(node) => {
                  itemRefs.current[item.citation.id] = node;
                }}
                className="scroll-mt-24 space-y-3"
              >
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
