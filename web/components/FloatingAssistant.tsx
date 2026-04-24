"use client";

import { useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { MessageSquare, Send, Sparkles } from "lucide-react";
import { cn, repoPath } from "@/lib/utils";
import { Button } from "./ui/button";

interface FloatingAssistantProps {
  repoId: string;
}

export function FloatingAssistant({ repoId }: FloatingAssistantProps) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"chat" | "research">("chat");
  const router = useRouter();
  const params = useParams();

  const owner = typeof params.owner === "string" ? params.owner : "";
  const repo = typeof params.repo === "string" ? params.repo : "";

  if (!owner || !repo) return null;

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!query.trim()) return;

    const basePath = repoPath(owner, repo, repoId);
    const targetPath = mode === "chat" ? `${basePath}/chat` : `${basePath}/research`;
    
    router.push(`${targetPath}?q=${encodeURIComponent(query.trim())}`);
    setQuery("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="fixed bottom-10 left-1/2 -translate-x-1/2 z-50 w-full max-w-5xl px-4">
      <div className="z-20 w-full rounded-2xl backdrop-blur-md transition-shadow focus-within:ring-2 focus-within:ring-indigo-500/20 bg-white/70 border border-indigo-300/20 shadow-lg shadow-indigo-500/20 p-2 flex flex-col gap-1.5">
        <div className="flex items-center gap-1 px-1" role="radiogroup" aria-label="Assistant mode">
          <Button
            variant="ghost"
            size="sm"
            role="radio"
            aria-checked={mode === "chat"}
            onClick={() => setMode("chat")}
            className={cn(
              "h-8 gap-1.5 text-xs font-medium rounded-lg px-3",
              mode === "chat" 
                ? "bg-indigo-50 text-indigo-600 hover:bg-indigo-100 hover:text-indigo-700" 
                : "text-muted-foreground hover:bg-slate-100"
            )}
          >
            <MessageSquare className="h-3.5 w-3.5" />
            Chat
          </Button>
          <Button
            variant="ghost"
            size="sm"
            role="radio"
            aria-checked={mode === "research"}
            onClick={() => setMode("research")}
            className={cn(
              "h-8 gap-1.5 text-xs font-medium rounded-lg px-3",
              mode === "research" 
                ? "bg-indigo-50 text-indigo-600 hover:bg-indigo-100 hover:text-indigo-700" 
                : "text-muted-foreground hover:bg-slate-100"
            )}
          >
            <Sparkles className="h-3.5 w-3.5" />
            Research
          </Button>
        </div>

        <form onSubmit={handleSubmit} className="relative flex items-end px-1 pb-1">
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            aria-label={mode === "chat" ? "Ask a question about the codebase" : "Enter complex logic or dependencies to investigate"}
            placeholder={mode === "chat" ? "Ask anything about the codebase..." : "Investigate complex logic or dependencies..."}
            className="w-full bg-transparent border-none focus:ring-0 text-sm py-1.5 px-2 pr-12 placeholder:text-slate-400 resize-none min-h-[52px] max-h-32 overflow-y-auto"
          />
          <Button
            type="submit"
            size="icon"
            aria-label="Send"
            disabled={!query.trim()}
            className={cn(
              "absolute right-1 bottom-1 h-8 w-8 rounded-xl transition-all",
              query.trim() 
                ? "bg-indigo-600 hover:bg-indigo-700 text-white shadow-md shadow-indigo-200" 
                : "bg-slate-100 text-slate-300"
            )}
          >
            <Send className="h-4 w-4" />
          </Button>
        </form>
      </div>
    </div>
  );
}
