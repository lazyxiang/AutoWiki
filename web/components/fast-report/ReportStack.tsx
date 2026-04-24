import type { FastReportSection as FastReportSectionData } from "@/lib/api";

import { ReportSection } from "./ReportSection";

type SortableSection = Pick<FastReportSectionData, "id" | "created_at">;

export function sortSectionsForDisplay<T extends SortableSection>(sections: T[]) {
  return [...sections]
    .sort((left, right) => {
      const byCreatedAt =
        new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
      if (byCreatedAt !== 0) {
        return byCreatedAt;
      }
      return left.id.localeCompare(right.id);
    })
    .map((section) => section.id);
}

export function ReportStack({
  sections,
  activeSectionId,
  isRunning,
}: {
  sections: FastReportSectionData[];
  activeSectionId?: string | null;
  isRunning?: boolean;
}) {
  const sortedIds = sortSectionsForDisplay(sections);
  const sortedSections = sortedIds
    .map((id) => sections.find((section) => section.id === id) ?? null)
    .filter((section): section is FastReportSectionData => section !== null);

  if (sortedSections.length === 0) {
    return (
      <section className="max-w-3xl">
        <p className="text-sm leading-6 text-slate-600">
          Ask a question from the assistant to start a fast report. Each follow-up
          will append a new section to this workspace.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-10 sm:space-y-12">
      {sortedSections.map((section) => (
        <ReportSection
          key={section.id}
          section={section}
          isActive={section.id === activeSectionId}
        />
      ))}

      {isRunning ? (
        <div className="max-w-3xl border-l border-dashed border-slate-200 pl-5 text-sm leading-6 text-slate-500 sm:pl-7">
          The next section is still streaming into this report.
        </div>
      ) : null}
    </section>
  );
}
