import type { Components } from "react-markdown";
import type { FastReportSection as FastReportSectionData } from "@/lib/api";
import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { MermaidBlock } from "@/components/WikiPage";

import { CitationLink } from "./CitationLink";

const STATUS_LABELS: Record<string, string> = {
  pending: "Generating",
  running: "Generating",
  complete: "Ready",
  done: "Ready",
  error: "Issue",
};

const CITATION_GROUP_RE = /\[([A-Za-z0-9_-]+(?:,\s*[A-Za-z0-9_-]+)*)\]/g;

export function injectCitationLinks(markdown: string, citationIds: Set<string>) {
  let inFence = false;

  return markdown
    .split("\n")
    .map((line) => {
      if (line.trimStart().startsWith("```")) {
        inFence = !inFence;
        return line;
      }

      if (inFence) {
        return line;
      }

      return line.replace(CITATION_GROUP_RE, (match, content: string) => {
        const ids = content.split(",").map((id) => id.trim());
        if (!ids.every((id) => citationIds.has(id))) {
          return match;
        }
        return ids.map((id) => `[${id}](#evidence-${id})`).join(" ");
      });
    })
    .join("\n");
}

export function ReportSection({
  section,
  isActive = false,
}: {
  section: FastReportSectionData;
  isActive?: boolean;
}) {
  const hasBody = section.markdown.trim().length > 0;
  const label = STATUS_LABELS[section.status] ?? section.status;
  const citationIds = new Set(section.citations.map((citation) => citation.id));
  const markdown = injectCitationLinks(section.markdown, citationIds);
  const citationIndex = new Map(
    section.citations.map((citation, index) => [citation.id, index]),
  );
  const citationById = new Map(
    section.citations.map((citation) => [citation.id, citation]),
  );
  const components: Components = {
    a({ href, children }) {
      if (href?.startsWith("#evidence-")) {
        const citationId = href.replace("#evidence-", "");
        const citation = citationById.get(citationId);
        const index = citationIndex.get(citationId);

        if (!citation || index === undefined) {
          return <>{children}</>;
        }

        return (
          <CitationLink
            citation={citation}
            index={index}
            sectionId={section.id}
          />
        );
      }

      return (
        <a
          href={href}
          className="text-slate-700 underline decoration-slate-300 underline-offset-4 transition-colors hover:text-slate-950 hover:decoration-slate-500"
        >
          {children}
        </a>
      );
    },
    code({ className, children, ...props }) {
      const match = /language-(\w+)/.exec(className || "");
      const language = match?.[1];
      const text = String(children).replace(/\n$/, "");

      if (language === "mermaid") {
        return <MermaidBlock>{text}</MermaidBlock>;
      }

      if (!className) {
        return (
          <code
            className="rounded bg-slate-100 px-1.5 py-0.5 text-sm text-slate-900"
            {...props}
          >
            {children}
          </code>
        );
      }

      return (
        <code className={className} {...props}>
          {children}
        </code>
      );
    },
  };

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
            components={components}
          >
            {markdown}
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
