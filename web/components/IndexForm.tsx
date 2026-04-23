"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { submitRepo } from "@/lib/api";
import { parseRepoUrl } from "@/lib/repo-search";

interface IndexFormProps {
  wikiLanguage?: string;
  reuseIndex?: boolean;
  reusePlan?: boolean;
  onQueryChange?: (q: string) => void;
}

export function IndexForm({
  wikiLanguage = "en",
  reuseIndex = false,
  reusePlan = false,
  onQueryChange,
}: IndexFormProps) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  function handleChange(value: string) {
    setUrl(value);
    if (onQueryChange) {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => onQueryChange(value), 200);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const { repo_id, job_id } = await submitRepo(url, {
        wikiLanguage,
        reuseIndex,
        reusePlan,
      });
      const parsed = parseRepoUrl(url);
      const query = new URLSearchParams({ repo_id });
      if (parsed) {
        query.set("owner", parsed.owner);
        query.set("repo", parsed.name);
      }
      router.push(`/jobs/${job_id}?${query.toString()}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="w-full max-w-2xl mx-auto">
      <form onSubmit={handleSubmit} className="relative group">
        <div className="flex items-center gap-2 p-2 bg-white dark:bg-zinc-900 rounded-2xl border border-border shadow-sm focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary transition-all duration-200">
          <Input
            type="text"
            placeholder="Search or paste a repo URL (github.com, gitlab.com/self-hosted, bitbucket.org, gitee.com)"
            value={url}
            onChange={(e) => handleChange(e.target.value)}
            disabled={loading}
            className="flex-1 h-12 border-none shadow-none focus-visible:ring-0 text-lg bg-transparent px-4 font-normal"
          />
          <Button
            type="submit"
            disabled={loading || !url.trim()}
            className="h-12 px-8 rounded-xl text-lg font-semibold shadow-sm hover:shadow transition-all"
          >
            {loading ? "Submitting…" : "Get Started"}
          </Button>
        </div>
        {error && (
          <p className="mt-3 text-destructive text-sm text-center font-medium">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}
