"use client";

import { useState } from "react";
import { MessageCircle, X } from "lucide-react";
import ChatPanel from "./ChatPanel";
import { Button } from "./ui/button";
import { cn } from "@/lib/utils";

interface ChatDrawerProps {
  repoId: string;
}

export function ChatDrawer({ repoId }: ChatDrawerProps) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      {/* FAB */}
      <Button
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "fixed bottom-6 right-6 h-14 w-14 rounded-full shadow-2xl transition-all duration-300 z-50",
          isOpen ? "bg-slate-200 text-slate-900 hover:bg-slate-300" : "bg-indigo-600 text-white hover:bg-indigo-700"
        )}
        size="icon"
      >
        {isOpen ? <X className="h-6 w-6" /> : <MessageCircle className="h-6 w-6" />}
      </Button>

      {/* Drawer Overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/20 backdrop-blur-sm transition-opacity z-40"
          onClick={() => setIsOpen(false)}
        />
      )}

      {/* Drawer Content */}
      <aside
        className={cn(
          "fixed top-0 right-0 h-full w-[400px] max-w-[90vw] bg-white shadow-2xl transform transition-transform duration-300 ease-in-out z-40 flex flex-col border-l border-slate-200",
          isOpen ? "translate-x-0" : "translate-x-full"
        )}
      >
        <div className="p-4 border-b border-slate-200 flex items-center justify-between bg-slate-50">
          <div>
            <h2 className="text-lg font-semibold text-slate-900 leading-none">AutoWiki Chat</h2>
            <p className="text-xs text-slate-500 mt-1">Ask about this codebase</p>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={() => setIsOpen(false)}>
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex-1 overflow-hidden">
          <ChatPanel repoId={repoId} />
        </div>
      </aside>
    </>
  );
}
