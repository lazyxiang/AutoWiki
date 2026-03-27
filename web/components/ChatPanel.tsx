"use client";
import { useState, useCallback, useEffect, useRef } from "react";
import { createChatSession } from "@/lib/api";
import { useChatStream } from "@/lib/ws";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export default function ChatPanel({ repoId }: { repoId: string }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const streamingRef = useRef("");

  useEffect(() => {
    createChatSession(repoId).then((d) => setSessionId(d.session_id));
  }, [repoId]);

  const handleChunk = useCallback((chunk: string) => {
    streamingRef.current += chunk;
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant" && last.content !== streamingRef.current) {
        return [...prev.slice(0, -1), { role: "assistant", content: streamingRef.current }];
      }
      return [...prev, { role: "assistant" as const, content: chunk }];
    });
  }, []);

  const handleDone = useCallback(() => {
    setStreaming(false);
    streamingRef.current = "";
  }, []);

  const handleError = useCallback((err: string) => {
    setStreaming(false);
    setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${err}` }]);
  }, []);

  const { sendMessage } = useChatStream(repoId, sessionId, handleChunk, handleDone, handleError);

  const submit = () => {
    if (!input.trim() || streaming) return;
    setMessages((prev) => [...prev, { role: "user", content: input }]);
    setStreaming(true);
    sendMessage(input);
    setInput("");
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "1rem" }}>
      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "0.75rem" }}>
        {messages.map((m, i) => (
          <div key={i} style={{
            alignSelf: m.role === "user" ? "flex-end" : "flex-start",
            background: m.role === "user" ? "#1e40af" : "#1f2937",
            color: "#f9fafb",
            padding: "0.75rem 1rem",
            borderRadius: "0.5rem",
            maxWidth: "80%",
            whiteSpace: "pre-wrap",
          }}>
            {m.content}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          disabled={streaming || !sessionId}
          placeholder="Ask about this codebase..."
          style={{ flex: 1, padding: "0.5rem", borderRadius: "0.25rem", background: "#374151", color: "#f9fafb", border: "1px solid #4b5563" }}
        />
        <button
          onClick={submit}
          disabled={streaming || !sessionId}
          style={{ padding: "0.5rem 1rem", background: "#2563eb", color: "white", borderRadius: "0.25rem", cursor: "pointer" }}
        >
          {streaming ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
