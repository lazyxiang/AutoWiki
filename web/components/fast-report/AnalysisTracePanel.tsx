import type { FastReportAnalysisTrace } from "@/lib/api";

const PHASE_LABELS: Record<string, string> = {
  planning: "Planning",
  retrieving_code_evidence: "Retrieving code evidence",
  expanding_call_chain: "Expanding call chain",
  ranking_evidence: "Ranking evidence",
  synthesizing: "Synthesizing answer",
};

export function AnalysisTracePanel({ trace }: { trace: FastReportAnalysisTrace }) {
  const files = trace.files ?? [];
  const phaseLabel = (trace.phase ? (PHASE_LABELS[trace.phase] ?? trace.phase) : null);

  return (
    <div className="space-y-3">
      {phaseLabel ? (
        <div className="flex items-center gap-2 text-sm text-slate-600">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-slate-400" />
          {phaseLabel}
        </div>
      ) : null}
      {files.length > 0 ? (
        <ul className="space-y-1">
          {files.map((file) => (
            <li key={file.path} className="flex items-start gap-2 text-sm">
              <span className="mt-0.5 shrink-0 font-mono text-xs text-slate-400">
                {file.role}
              </span>
              <span className="font-mono text-xs text-slate-700">{file.path}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
