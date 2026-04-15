"use client";
import { useCallback, useEffect, useRef, useState } from "react";

const WS_URL = process.env.NEXT_PUBLIC_API_URL?.replace("http", "ws") ?? "ws://localhost:3001";

export function useJobProgress(jobId: string | null) {
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<string>("queued");
  const [statusDescription, setStatusDescription] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!jobId) return;
    ws.current = new WebSocket(`${WS_URL}/ws/jobs/${jobId}`);
    ws.current.onmessage = (e) => {
      const data = JSON.parse(e.data);
      setProgress(data.progress ?? 0);
      setStatus(data.status ?? "running");
      setStatusDescription(data.status_description ?? null);
      setRetrying(data.retrying ?? false);
    };
    return () => ws.current?.close();
  }, [jobId]);

  return { progress, status, statusDescription, retrying };
}

export function useChatStream(
  repoId: string,
  sessionId: string | null,
  onChunk: (chunk: string) => void,
  onDone: () => void,
  onError: (err: string) => void,
) {
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, []);

  const sendMessage = useCallback(
    (content: string) => {
      if (!sessionId) {
        onError("Chat session is still initializing. Please retry.");
        return;
      }
      // Close any existing connection before opening a new one
      if (wsRef.current) {
        wsRef.current.onmessage = null;
        wsRef.current.onerror = null;
        wsRef.current.close();
        wsRef.current = null;
      }
      const ws = new WebSocket(`${WS_URL}/ws/repos/${repoId}/chat/${sessionId}`);
      wsRef.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ content }));
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "chunk") onChunk(msg.content);
        else if (msg.type === "done") { onDone(); ws.close(); }
        else if (msg.type === "error") { onError(msg.content); ws.close(); }
      };
      ws.onerror = () => onError("WebSocket error");
    },
    [repoId, sessionId, onChunk, onDone, onError],
  );

  return { sendMessage };
}

interface ResearchPlanStep {
  query: string;
  rationale: string;
}
interface ResearchFinding {
  step_index: number;
  answer: string;
  sources: Array<{ file: string }>;
}

export function useResearchStream(
  repoId: string,
  jobId: string | null,
  onPlan: (plan: ResearchPlanStep[]) => void,
  onStep: (finding: ResearchFinding) => void,
  onReport: (markdown: string) => void,
  onDone: () => void,
  onError: (msg: string) => void,
) {
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!jobId) return;
    const ws = new WebSocket(`${WS_URL}/ws/repos/${repoId}/research/${jobId}`);
    wsRef.current = ws;
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data) as {
        type: string;
        plan?: ResearchPlanStep[];
        step_index?: number;
        answer?: string;
        sources?: Array<{ file: string }>;
        content?: string;
      };
      if (msg.type === "plan") onPlan(msg.plan ?? []);
      else if (msg.type === "step_finding")
        onStep({
          step_index: msg.step_index ?? 0,
          answer: msg.answer ?? "",
          sources: msg.sources ?? [],
        });
      else if (msg.type === "report") onReport(msg.content ?? "");
      else if (msg.type === "done") {
        onDone();
        ws.close();
      } else if (msg.type === "error") {
        onError(msg.content ?? "Unknown error");
        ws.close();
      }
    };
    ws.onerror = () => onError("WebSocket error");
    return () => {
      ws.close();
    };
  }, [repoId, jobId, onPlan, onStep, onReport, onDone, onError]);
}
