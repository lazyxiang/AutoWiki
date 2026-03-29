"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { createChatSession } from "@/lib/api";
import { useChatStream } from "@/lib/ws";
import { Send, Loader2, Bot, User } from "lucide-react";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { ScrollArea } from "./ui/scroll-area";
import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";

interface Message {
  role: "user" | "assistant";
  content: string;
}

/**
 * A conversational interface for interacting with the codebase AI.
 * Supports streaming responses and auto-scrolling.
 * 
 * @param repoId - The ID of the repository to chat about.
 */
export default function ChatPanel({ repoId }: { repoId: string }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const streamingRef = useRef("");
  const viewportRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    createChatSession(repoId).then((d) => setSessionId(d.session_id));
  }, [repoId]);

  /**
   * Scrolls the chat viewport to the bottom.
   */
  const scrollToBottom = useCallback(() => {
    if (viewportRef.current) {
      viewportRef.current.scrollTop = viewportRef.current.scrollHeight;
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  /**
   * Handles incoming chunks of the AI's response stream.
   */
  const handleChunk = useCallback((chunk: string) => {
    streamingRef.current += chunk;
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant") {
        return [...prev.slice(0, -1), { role: "assistant", content: streamingRef.current }];
      }
      return [...prev, { role: "assistant" as const, content: chunk }];
    });
  }, []);

  /**
   * Called when the response stream completes successfully.
   */
  const handleDone = useCallback(() => {
    setStreaming(false);
    streamingRef.current = "";
  }, []);

  /**
   * Handles errors during streaming by clearing the buffer and showing an error message.
   */
  const handleError = useCallback((err: string) => {
    setStreaming(false);
    streamingRef.current = "";
    setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${err}` }]);
  }, []);

  const { sendMessage } = useChatStream(repoId, sessionId, handleChunk, handleDone, handleError);

  /**
   * Submits the user's message to the chat stream.
   */
  const submit = () => {
    if (!input.trim() || streaming) return;
    setMessages((prev) => [...prev, { role: "user", content: input }]);
    setStreaming(true);
    sendMessage(input);
    setInput("");
  };

  return (
    <div className="flex flex-col h-full bg-white">
      <ScrollArea className="flex-1 p-4" viewportRef={viewportRef}>
        <div className="flex flex-col gap-4">
          {messages.length === 0 && (
            <div className="text-center py-20 px-6">
              <div className="bg-indigo-50 h-16 w-16 rounded-2xl flex items-center justify-center mx-auto mb-4 border border-indigo-100">
                <Bot className="h-8 w-8 text-indigo-500" />
              </div>
              <h3 className="text-slate-900 font-semibold mb-1">Codebase Assistant</h3>
              <p className="text-slate-400 text-sm">Ask about module dependencies, file structures, or specific logic.</p>
            </div>
          )}
          {messages.map((m, i) => (
            <div
              key={i}
              className={cn(
                "flex gap-3 max-w-[90%]",
                m.role === "user" ? "ml-auto flex-row-reverse" : "mr-auto"
              )}
            >
              <div className={cn(
                "h-8 w-8 rounded-full flex items-center justify-center shrink-0 border",
                m.role === "user" ? "bg-indigo-50 border-indigo-100" : "bg-slate-50 border-slate-100"
              )}>
                {m.role === "user" ? (
                  <User className="h-4 w-4 text-indigo-600" />
                ) : (
                  <Bot className="h-4 w-4 text-slate-600" />
                )}
              </div>
              <div
                className={cn(
                  "px-4 py-2 rounded-2xl text-sm leading-relaxed",
                  m.role === "user"
                    ? "bg-indigo-600 text-white rounded-tr-none shadow-sm whitespace-pre-wrap"
                    : "bg-slate-100 text-slate-900 rounded-tl-none border border-slate-200 wiki-content"
                )}
              >
                {m.role === "assistant" ? (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    rehypePlugins={[rehypeHighlight]}
                  >
                    {m.content}
                  </ReactMarkdown>
                ) : (
                  m.content
                )}
              </div>
            </div>
          ))}
          {streaming && messages[messages.length - 1]?.role !== "assistant" && (
            <div className="flex gap-3 mr-auto max-w-[90%]">
              <div className="h-8 w-8 rounded-full flex items-center justify-center shrink-0 border bg-slate-50 border-slate-100">
                <Bot className="h-4 w-4 text-slate-600" />
              </div>
              <div className="px-4 py-2 rounded-2xl rounded-tl-none bg-slate-100 text-slate-900 border border-slate-200">
                <Loader2 className="h-4 w-4 animate-spin text-slate-400" />
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
      
      <div className="p-4 border-t border-slate-100 bg-slate-50/50">
        <div className="relative flex items-center">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            disabled={streaming || !sessionId}
            placeholder="Ask anything about the codebase..."
            className="pr-10 bg-white border-slate-200 focus-visible:ring-indigo-500 rounded-xl min-h-[44px]"
          />
          <Button
            size="icon-sm"
            onClick={submit}
            disabled={streaming || !sessionId || !input.trim()}
            className={cn(
              "absolute right-1 transition-all",
              input.trim() ? "bg-indigo-600 hover:bg-indigo-700 text-white" : "bg-slate-200 text-slate-400"
            )}
          >
            {streaming ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </div>
  );
}
