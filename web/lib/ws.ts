"use client";
import { useEffect, useRef, useState } from "react";

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
