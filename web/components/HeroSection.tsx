"use client";
import { useState } from "react";
import Link from "next/link";
import { Settings } from "lucide-react";
import { IndexForm } from "@/components/IndexForm";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";

interface DebugToggleProps {
  label: string;
  checked: boolean;
  onToggle: () => void;
  title: string;
  ariaLabel: string;
  activeColorClass: string;
  activeDotClass: string;
}

function DebugToggle({ label, checked, onToggle, title, ariaLabel, activeColorClass, activeDotClass }: DebugToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      onClick={onToggle}
      title={title}
      className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors ${
        checked ? activeColorClass : "border-border bg-background text-muted-foreground hover:bg-muted"
      }`}
    >
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${checked ? activeDotClass : "bg-muted-foreground/40"}`} />
      {label}
    </button>
  );
}

interface HeroSectionProps {
  onQueryChange?: (q: string) => void;
}

export function HeroSection({ onQueryChange }: HeroSectionProps) {
  const [wikiLanguage, setWikiLanguage] = useState("en");
  const [reuseIndex, setReuseIndex] = useState(false);
  const [reusePlan, setReusePlan] = useState(false);

  return (
    <section className="relative pt-24 pb-16 px-6 text-center border-b border-dashed">
      <div className="absolute top-4 right-6 flex items-center gap-3">
        <DebugToggle
          label="Reuse Index"
          checked={reuseIndex}
          onToggle={() => setReuseIndex((v) => !v)}
          title="Deprecated: ignored by keyword retrieval"
          ariaLabel="Deprecated reuse index option"
          activeColorClass="border-amber-400 bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
          activeDotClass="bg-amber-500"
        />
        <DebugToggle
          label="Reuse Plan"
          checked={reusePlan}
          onToggle={() => setReusePlan((v) => !v)}
          title="Debug: skip Stage 5 (Wiki Planner) and reuse the existing plan"
          ariaLabel="Reuse existing wiki plan (skip planning stage)"
          activeColorClass="border-violet-400 bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300"
          activeDotClass="bg-violet-500"
        />
        <LanguageSwitcher value={wikiLanguage} onChange={setWikiLanguage} />
        <Link
          href="/settings"
          title="Settings"
          aria-label="Open settings"
          className="p-1.5 rounded-lg border border-border bg-background text-muted-foreground hover:bg-muted transition-colors"
        >
          <Settings size={16} />
        </Link>
      </div>
      <h1 className="text-5xl font-extrabold tracking-tight text-foreground">
        Explore Open Source Knowledge
      </h1>
      <p className="mt-4 text-xl text-muted-foreground max-w-2xl mx-auto">
        AI-powered wiki generator for any GitHub, GitLab, or Bitbucket repository.
        Search for a repo or paste a link to get started.
      </p>
      <div className="mt-10 max-w-xl mx-auto">
        <IndexForm
          wikiLanguage={wikiLanguage}
          reuseIndex={reuseIndex}
          reusePlan={reusePlan}
          onQueryChange={onQueryChange}
        />
      </div>
    </section>
  );
}
