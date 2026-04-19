"use client";
import { useState } from "react";
import { IndexForm } from "@/components/IndexForm";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";

export function HeroSection() {
  const [wikiLanguage, setWikiLanguage] = useState("en");
  const [reuseIndex, setReuseIndex] = useState(false);
  const [reusePlan, setReusePlan] = useState(false);

  return (
    <section className="relative pt-24 pb-16 px-6 text-center border-b border-dashed">
      <div className="absolute top-4 right-6 flex items-center gap-3">
        {/* Reuse Index debug toggle */}
        <button
          type="button"
          role="switch"
          aria-checked={reuseIndex}
          aria-label="Reuse existing FAISS index (skip embedding stage)"
          onClick={() => setReuseIndex((v) => !v)}
          title="Debug: skip Stage 4 (RAG embedding) and reuse the existing FAISS index"
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors ${
            reuseIndex
              ? "border-amber-400 bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
              : "border-border bg-background text-muted-foreground hover:bg-muted"
          }`}
        >
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${reuseIndex ? "bg-amber-500" : "bg-muted-foreground/40"}`} />
          Reuse Index
        </button>
        {/* Reuse Plan debug toggle */}
        <button
          type="button"
          role="switch"
          aria-checked={reusePlan}
          aria-label="Reuse existing wiki plan (skip planning stage)"
          onClick={() => setReusePlan((v) => !v)}
          title="Debug: skip Stage 5 (Wiki Planner) and reuse the existing plan"
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors ${
            reusePlan
              ? "border-violet-400 bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300"
              : "border-border bg-background text-muted-foreground hover:bg-muted"
          }`}
        >
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${reusePlan ? "bg-violet-500" : "bg-muted-foreground/40"}`} />
          Reuse Plan
        </button>
        <LanguageSwitcher value={wikiLanguage} onChange={setWikiLanguage} />
      </div>
      <h1 className="text-5xl font-extrabold tracking-tight text-foreground">
        Explore Open Source Knowledge
      </h1>
      <p className="mt-4 text-xl text-muted-foreground max-w-2xl mx-auto">
        AI-powered wiki generator for any GitHub repository. Search for a repo or paste a link to get started.
      </p>
      <div className="mt-10 max-w-xl mx-auto">
        <IndexForm wikiLanguage={wikiLanguage} reuseIndex={reuseIndex} reusePlan={reusePlan} />
      </div>
    </section>
  );
}
