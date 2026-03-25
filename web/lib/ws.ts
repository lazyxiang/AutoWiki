"use client";
import { useCallback, useEffect, useRef, useState } from "react";

const WS_URL = process.env.NEXT_PUBLIC_API_URL?.replace("http", "ws") ?? "ws://localhost:3001";

export function useJobProgress(jobId: string | null) {
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<string>("queued");
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!jobId) return;
    ws.current = new WebSocket(`${WS_URL}/ws/jobs/${jobId}`);
    ws.current.onmessage = (e) => {
      const data = JSON.parse(e.data);
      setProgress(data.progress ?? 0);
      setStatus(data.status ?? "running");
    };
    return () => ws.current?.close();
  }, [jobId]);

  return { progress, status };
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
      if (!sessionId) return;
      const wsBase = typeof window !== "undefined"
        ? window.location.origin.replace(/^http/, "ws").replace(":3000", ":3001")
        : "ws://localhost:3001";
      const ws = new WebSocket(`${wsBase}/ws/repos/${repoId}/chat/${sessionId}`);
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
