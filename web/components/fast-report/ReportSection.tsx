import type { FastReportSection as FastReportSectionData } from "@/lib/api";
import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

const STATUS_LABELS: Record<string, string> = {
  pending: "Generating",
  running: "Generating",
  complete: "Ready",
  done: "Ready",
  error: "Issue",
};

export function ReportSection({
  section,
  isActive = false,
}: {
  section: FastReportSectionData;
  isActive?: boolean;
}) {
  const hasBody = section.markdown.trim().length > 0;
  const label = STATUS_LABELS[section.status] ?? section.status;

  return (
    <article
      className={cn(
        "border-l pl-5 sm:pl-7",
        isActive ? "border-slate-900" : "border-slate-200",
      )}
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[0.68rem] font-semibold uppercase tracking-[0.24em] text-slate-500">
          {label}
        </span>
        <h2 className="text-xl font-semibold tracking-tight text-slate-950 sm:text-2xl">
          {section.title}
        </h2>
      </div>

      {section.summary ? (
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600 sm:text-[0.95rem]">
          {section.summary}
        </p>
      ) : null}

      {hasBody ? (
        <div className="wiki-content mt-5 max-w-3xl text-[0.98rem] leading-7 text-slate-800">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
          >
            {section.markdown}
          </ReactMarkdown>
        </div>
      ) : (
        <div className="mt-5 max-w-3xl rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 px-4 py-5 text-sm leading-6 text-slate-500">
          This section is still assembling code-grounded findings.
        </div>
      )}
    </article>
  );
}
