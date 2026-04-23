"use client";

import { useCallback, useState, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import {
  startResearch,
  type ResearchFinding,
  type ResearchPlanStep,
} from "@/lib/api";
import { useResearchStream } from "@/lib/ws";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

export default function ResearchPanel({ repoId }: { repoId: string }) {
  const [question, setQuestion] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [plan, setPlan] = useState<ResearchPlanStep[]>([]);
  const [findings, setFindings] = useState<ResearchFinding[]>([]);
  const [report, setReport] = useState<string>("");
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">(
    "idle",
  );
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const searchParams = useSearchParams();
  const initialQueryHandled = useRef(false);

  const onPlan = useCallback((p: ResearchPlanStep[]) => setPlan(p), []);
  const onStep = useCallback((f: ResearchFinding) => {
    setFindings((prev) => [...prev, f]);
  }, []);
  const onReport = useCallback((md: string) => setReport(md), []);
  const onDone = useCallback(() => setStatus("done"), []);
  const onError = useCallback((msg: string) => {
    setErrorMsg(msg);
    setStatus("error");
  }, []);

  useResearchStream(repoId, jobId, onPlan, onStep, onReport, onDone, onError);

  const submit = useCallback(async (query?: string) => {
    const text = query || question;
    if (!text.trim()) return;
    setPlan([]);
    setFindings([]);
    setReport("");
    setErrorMsg(null);
    setStatus("running");
    try {
      const { job_id } = await startResearch(repoId, text.trim());
      setJobId(job_id);
      if (!query) setQuestion("");
    } catch (e) {
      setErrorMsg(String(e));
      setStatus("error");
    }
  }, [question, repoId]);

  // Handle initial query from URL
  useEffect(() => {
    if (!initialQueryHandled.current) {
      const q = searchParams.get("q");
      if (q) {
        initialQueryHandled.current = true;
        // Using a tick to avoid cascading render lint error
        setTimeout(() => {
          setQuestion(q);
          submit(q);
        }, 0);
      }
    }
  }, [searchParams, submit]);

  return (
    <div className="flex flex-col gap-4 p-4 max-w-3xl mx-auto">
      <div className="flex gap-2">
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What do you want to investigate?"
          disabled={status === "running"}
        />
        <Button
          onClick={() => submit()}
          disabled={status === "running" || !question.trim()}
        >
          Research
        </Button>
      </div>

      {status === "error" && errorMsg && (
        <div className="text-red-600 text-sm">Error: {errorMsg}</div>
      )}

      {plan.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-2">Plan</h2>
          <ol className="list-decimal pl-6 space-y-1">
            {plan.map((s, i) => (
              <li key={i}>
                <span className="font-medium">{s.query}</span> — {s.rationale}
              </li>
            ))}
          </ol>
        </section>
      )}

      {findings.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-2">Findings</h2>
          <div className="space-y-3">
            {findings.map((f) => (
              <div key={f.step_index} className="border rounded p-3 bg-slate-50">
                <div className="font-medium mb-1">Step {f.step_index + 1}</div>
                <div className="wiki-content">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    rehypePlugins={[rehypeHighlight]}
                  >
                    {f.answer}
                  </ReactMarkdown>
                </div>
                {f.sources.length > 0 && (
                  <div className="mt-1 text-xs text-slate-500">
                    Sources: {f.sources.map((s) => s.file).join(", ")}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {report && (
        <section>
          <h2 className="text-lg font-semibold mb-2">Final Report</h2>
          <div className="wiki-content border rounded p-4 bg-white">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeHighlight]}
            >
              {report}
            </ReactMarkdown>
          </div>
        </section>
      )}
    </div>
  );
}
