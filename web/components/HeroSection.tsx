"use client";
import { useState } from "react";
import { IndexForm } from "@/components/IndexForm";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";

export function HeroSection() {
  const [wikiLanguage, setWikiLanguage] = useState("en");

  return (
    <section className="relative pt-24 pb-16 px-6 text-center border-b border-dashed">
      <div className="absolute top-4 right-6">
        <LanguageSwitcher value={wikiLanguage} onChange={setWikiLanguage} />
      </div>
      <h1 className="text-5xl font-extrabold tracking-tight text-foreground">
        Explore Open Source Knowledge
      </h1>
      <p className="mt-4 text-xl text-muted-foreground max-w-2xl mx-auto">
        AI-powered wiki generator for any GitHub repository. Search for a repo or paste a link to get started.
      </p>
      <div className="mt-10 max-w-xl mx-auto">
        <IndexForm wikiLanguage={wikiLanguage} />
      </div>
    </section>
  );
}
