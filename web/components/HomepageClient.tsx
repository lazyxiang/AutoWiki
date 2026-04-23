"use client";
import { useState } from "react";
import { HeroSection } from "@/components/HeroSection";
import { RepoGrid } from "@/components/RepoGrid";
import type { Repository } from "@/lib/api";

export function HomepageClient({ repos }: { repos: Repository[] }) {
  const [query, setQuery] = useState("");
  return (
    <>
      <HeroSection onQueryChange={setQuery} />
      <RepoGrid repos={repos} query={query} />
    </>
  );
}
